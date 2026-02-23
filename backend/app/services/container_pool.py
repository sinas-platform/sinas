"""Container pool for isolated function execution.

Replaces per-user containers with a pool of pre-warmed generic containers
that any user's function can run in (acquire/release model).
"""
import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import docker
from docker.errors import NotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class PooledContainer:
    """A container managed by the pool."""

    name: str
    container_id: str
    executions: int = 0
    created_at: float = field(default_factory=time.time)


class ContainerPool:
    """
    Pool of pre-warmed Docker containers for untrusted function execution.

    Containers are generic (not user-specific) and recycled after a
    configurable number of executions or on error (tainted).
    """

    def __init__(self):
        self.client = docker.from_env()
        self.idle: deque[PooledContainer] = deque()
        self.in_use: dict[str, PooledContainer] = {}
        self._next_id: int = 1
        self._condition = asyncio.Condition()
        self._initialized = False
        self._replenish_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        self._replenish_event = asyncio.Event()
        self.docker_network = self._detect_network()

    def _detect_network(self) -> str:
        """Auto-detect Docker network."""
        network = settings.docker_network
        if network != "auto":
            return network

        try:
            import socket

            hostname = socket.gethostname()
            container = self.client.containers.get(hostname)
            networks = list(container.attrs["NetworkSettings"]["Networks"].keys())
            if networks:
                detected = networks[0]
                logger.info(f"Auto-detected Docker network: {detected}")
                return detected
        except Exception as e:
            logger.warning(f"Failed to auto-detect network: {e}")

        logger.warning("Using fallback network: bridge")
        return "bridge"

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, db: AsyncSession):
        """
        Initialize pool on startup (leader-only).

        Discovers existing pool containers, scales to pool_min_size,
        and starts background replenish + health check tasks.
        """
        if self._initialized:
            return

        await self._discover_existing_containers()

        # Scale up to min size
        current = len(self.idle) + len(self.in_use)
        if current < settings.pool_min_size:
            needed = settings.pool_min_size - current
            print(f"📦 Scaling pool to min size: creating {needed} containers")
            for _ in range(needed):
                try:
                    pc = await self._create_container(db)
                    self.idle.append(pc)
                except Exception as e:
                    print(f"❌ Failed to create pool container: {e}")

        # Start background tasks
        self._replenish_task = asyncio.create_task(self._replenish_loop(db))
        self._health_task = asyncio.create_task(self._health_check_loop())

        self._initialized = True
        print(
            f"✅ Container pool initialized: {len(self.idle)} idle, "
            f"{len(self.in_use)} in-use"
        )

    async def _discover_existing_containers(self):
        """
        Find running/stopped sinas-pool-* Docker containers.

        Restarts stopped ones and adds all to idle queue.
        Sets _next_id past the highest existing ID.
        """
        try:
            containers = await asyncio.to_thread(
                self.client.containers.list,
                all=True,
                filters={"name": "sinas-pool-"},
            )

            max_id = 0
            for container in containers:
                name = container.name
                match = re.match(r"^sinas-pool-(\d+)$", name)
                if not match:
                    continue

                num = int(match.group(1))
                max_id = max(max_id, num)

                if container.status != "running":
                    print(f"🔄 Starting stopped pool container: {name}")
                    try:
                        await asyncio.to_thread(container.start)
                        await asyncio.to_thread(container.reload)
                    except docker.errors.APIError as e:
                        print(f"⚠️  Cannot start {name}, removing: {e}")
                        try:
                            await asyncio.to_thread(container.remove, force=True)
                        except Exception:
                            pass
                        continue

                pc = PooledContainer(
                    name=name,
                    container_id=container.id,
                    created_at=time.time(),
                )
                self.idle.append(pc)
                print(f"🔍 Discovered pool container: {name} (status: {container.status})")

            self._next_id = max_id + 1

        except Exception as e:
            print(f"❌ Failed to discover existing pool containers: {e}")

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self, timeout: Optional[int] = None) -> PooledContainer:
        """
        Acquire an idle container from the pool.

        Waits up to `timeout` seconds if none available. Signals
        replenishment when the pool is low.
        """
        if timeout is None:
            timeout = settings.pool_acquire_timeout

        async with self._condition:
            deadline = time.monotonic() + timeout

            while not self.idle:
                # Signal replenisher to create more containers
                self._replenish_event.set()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"No pool container available within {timeout}s "
                        f"(idle=0, in_use={len(self.in_use)})"
                    )
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"No pool container available within {timeout}s "
                        f"(idle=0, in_use={len(self.in_use)})"
                    )

            pc = self.idle.popleft()
            self.in_use[pc.name] = pc

            # Trigger replenish if idle is getting low
            if len(self.idle) < settings.pool_min_idle:
                self._replenish_event.set()

            return pc

    async def release(self, name: str, tainted: bool = False):
        """
        Release a container back to the pool.

        If tainted (error during execution) or max executions reached,
        destroy and signal replenishment. Otherwise clean up IPC files
        and return to idle.
        """
        async with self._condition:
            pc = self.in_use.pop(name, None)
            if pc is None:
                logger.warning(f"Tried to release unknown container: {name}")
                return

            pc.executions += 1
            should_destroy = tainted or pc.executions >= settings.pool_max_executions

            if should_destroy:
                reason = "tainted" if tainted else "max executions reached"
                logger.info(f"Destroying pool container {name}: {reason}")
                await self._destroy_container(pc)
                self._replenish_event.set()
            else:
                # Clean up IPC files for next use
                try:
                    container = await asyncio.to_thread(
                        self.client.containers.get, name
                    )
                    await asyncio.to_thread(
                        container.exec_run,
                        cmd=["sh", "-c", "rm -f /tmp/exec_request.json /tmp/exec_result.json /tmp/exec_trigger"],
                    )
                except Exception as e:
                    logger.warning(f"Failed to clean IPC files in {name}: {e}")
                    # If we can't clean, destroy instead
                    await self._destroy_container(pc)
                    self._replenish_event.set()
                    self._condition.notify_all()
                    return

                self.idle.append(pc)
                self._condition.notify_all()

    # ------------------------------------------------------------------
    # Function execution (drop-in replacement for UserContainerManager)
    # ------------------------------------------------------------------

    async def execute_function(
        self,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        enabled_namespaces: list[str],
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: Optional[str],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Execute a function in a pooled container.

        Same signature as UserContainerManager.execute_function() for
        drop-in replacement.
        """
        # Fetch function code
        from app.models.function import Function

        result = await db.execute(
            select(Function).where(
                Function.namespace == function_namespace,
                Function.name == function_name,
                Function.is_active == True,
            )
        )
        function = result.scalar_one_or_none()

        if not function:
            return {
                "status": "failed",
                "error": f"Function {function_namespace}/{function_name} not found",
            }

        # Acquire a container
        acquire_start = time.time()
        pc = await self.acquire()
        acquire_elapsed = time.time() - acquire_start
        logger.info(
            f"Acquired pool container {pc.name} in {acquire_elapsed:.3f}s "
            f"for {function_namespace}/{function_name}"
        )

        tainted = False
        try:
            container = await asyncio.to_thread(self.client.containers.get, pc.name)

            # Same IPC protocol as the old per-user containers
            payload = {
                "action": "execute_inline",
                "function_code": function.code,
                "execution_id": execution_id,
                "function_namespace": function_namespace,
                "function_name": function_name,
                "enabled_namespaces": enabled_namespaces,
                "input_data": input_data,
                "context": {
                    "user_id": user_id,
                    "user_email": user_email,
                    "access_token": access_token,
                    "execution_id": execution_id,
                    "trigger_type": trigger_type,
                    "chat_id": chat_id,
                },
            }

            exec_start = time.time()
            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=[
                    "python3",
                    "-c",
                    f"""
import sys
import json
payload = json.loads({json.dumps(json.dumps(payload))})
# Write execution request
with open("/tmp/exec_request.json", "w") as f:
    json.dump(payload, f)
# Trigger execution
with open("/tmp/exec_trigger", "w") as f:
    f.write("1")
# Wait for result
import time
max_wait = {settings.function_timeout}
start = time.time()
while time.time() - start < max_wait:
    try:
        with open("/tmp/exec_result.json", "r") as f:
            result = json.load(f)
            # Clear files
            import os
            os.remove("/tmp/exec_result.json")
            os.remove("/tmp/exec_trigger")
            print(json.dumps(result))
            sys.exit(0)
    except FileNotFoundError:
        time.sleep(0.1)
        continue
print(json.dumps({{"error": "Execution timeout"}}))
sys.exit(1)
""",
                ],
                demux=True,
            )
            exec_elapsed = time.time() - exec_start
            logger.info(f"Pool container {pc.name} exec completed in {exec_elapsed:.3f}s")

            stdout, stderr = exec_result.output

            if exec_result.exit_code != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise Exception(f"Execution failed: {error_msg}")

            stdout_str = stdout.decode() if stdout else ""
            result = json.loads(stdout_str)

            if "error" in result:
                raise Exception(result["error"])

            return result

        except Exception as e:
            tainted = True
            logger.error(f"Error executing function in pool container {pc.name}: {e}")
            raise
        finally:
            await self.release(pc.name, tainted=tainted)

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    async def _create_container(self, db: AsyncSession) -> PooledContainer:
        """Create a new pool container with all approved packages installed."""
        name = f"sinas-pool-{self._next_id}"
        self._next_id += 1

        logger.info(f"Creating pool container: {name}")

        container_config = {
            "image": settings.function_container_image,
            "name": name,
            "detach": True,
            "network": self.docker_network,
            "mem_limit": f"{settings.max_function_memory}m",
            "nano_cpus": int(settings.max_function_cpu * 1_000_000_000),
            "cap_drop": ["ALL"],
            "cap_add": ["CHOWN", "SETUID", "SETGID"],
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": {"/tmp": "size=100m,mode=1777"},
            "environment": {
                "PYTHONUNBUFFERED": "1",
                "POOL_CONTAINER": "true",
            },
            "labels": {
                "sinas.type": "pool-executor",
                "sinas.pool": "true",
            },
            "restart_policy": {"Name": "unless-stopped"},
        }

        try:
            container = await asyncio.to_thread(
                self.client.containers.run,
                **container_config,
                storage_opt={"size": settings.max_function_storage},
            )
        except Exception as e:
            if "storage-opt" in str(e).lower() or "storage driver" in str(e).lower():
                logger.warning(
                    f"Storage limits not supported, continuing without storage_opt: {e}"
                )
                container = await asyncio.to_thread(
                    self.client.containers.run,
                    **container_config,
                )
            else:
                raise

        # Wait for container to be ready
        await asyncio.sleep(1)

        # Install all approved packages
        await self._install_packages(container, db)

        pc = PooledContainer(
            name=name,
            container_id=container.id,
        )
        logger.info(f"Created pool container: {name} ({container.id[:12]})")
        return pc

    async def _install_packages(self, container, db: AsyncSession):
        """Install all approved packages in a pool container."""
        from app.models.package import InstalledPackage

        try:
            result = await db.execute(select(InstalledPackage))
            approved_packages = result.scalars().all()

            if not approved_packages:
                logger.info("No approved packages to install in pool container")
                return

            packages_to_install = []
            for pkg in approved_packages:
                if pkg.version:
                    packages_to_install.append(f"{pkg.package_name}=={pkg.version}")
                else:
                    packages_to_install.append(pkg.package_name)

            logger.info(
                f"Installing {len(packages_to_install)} packages in pool container: "
                f"{', '.join(packages_to_install)}"
            )

            install_cmd = ["pip", "install", "--no-cache-dir"] + packages_to_install

            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=install_cmd,
                demux=True,
            )

            stdout, stderr = exec_result.output
            if exec_result.exit_code == 0:
                logger.info("Successfully installed packages in pool container")
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.warning(f"Package installation had issues: {error_msg}")

        except Exception as e:
            logger.error(f"Error installing packages in pool container: {e}")

    async def _destroy_container(self, pc: PooledContainer):
        """Stop and remove a pool container."""
        try:
            container = await asyncio.to_thread(self.client.containers.get, pc.name)
            await asyncio.to_thread(container.stop, timeout=10)
            await asyncio.to_thread(container.remove)
            logger.info(f"Destroyed pool container: {pc.name}")
        except NotFound:
            logger.info(f"Pool container already gone: {pc.name}")
        except Exception as e:
            logger.error(f"Error destroying pool container {pc.name}: {e}")

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _replenish_loop(self, _startup_db: AsyncSession):
        """Background task that creates containers when idle drops below threshold."""
        from app.core.database import AsyncSessionLocal

        while True:
            try:
                # Wait for signal or periodic check (30s)
                try:
                    await asyncio.wait_for(self._replenish_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                self._replenish_event.clear()

                total = len(self.idle) + len(self.in_use)

                # Create containers if idle is low and we haven't hit max
                while (
                    len(self.idle) < settings.pool_min_idle
                    and total < settings.pool_max_size
                ):
                    try:
                        async with AsyncSessionLocal() as db:
                            pc = await self._create_container(db)
                        async with self._condition:
                            self.idle.append(pc)
                            self._condition.notify_all()
                        total += 1
                    except Exception as e:
                        logger.error(f"Replenish failed to create container: {e}")
                        break

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Error in replenish loop: {e}")
                await asyncio.sleep(5)

    async def _health_check_loop(self):
        """Background task that verifies idle containers are alive (60s interval)."""
        while True:
            try:
                await asyncio.sleep(60)

                dead: list[PooledContainer] = []

                # Check all idle containers
                for pc in list(self.idle):
                    try:
                        container = await asyncio.to_thread(
                            self.client.containers.get, pc.name
                        )
                        await asyncio.to_thread(container.reload)
                        if container.status != "running":
                            dead.append(pc)
                    except NotFound:
                        dead.append(pc)
                    except Exception as e:
                        logger.warning(f"Health check error for {pc.name}: {e}")
                        dead.append(pc)

                if dead:
                    async with self._condition:
                        for pc in dead:
                            try:
                                self.idle.remove(pc)
                            except ValueError:
                                pass
                            await self._destroy_container(pc)
                        logger.info(f"Health check removed {len(dead)} dead containers")
                    self._replenish_event.set()

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")

    # ------------------------------------------------------------------
    # Admin operations
    # ------------------------------------------------------------------

    async def scale(self, target: int, db: AsyncSession) -> dict[str, Any]:
        """Scale the pool to a target number of total containers."""
        current = len(self.idle) + len(self.in_use)

        if target > settings.pool_max_size:
            target = settings.pool_max_size

        if target > current:
            added = 0
            for _ in range(target - current):
                try:
                    pc = await self._create_container(db)
                    async with self._condition:
                        self.idle.append(pc)
                        self._condition.notify_all()
                    added += 1
                except Exception as e:
                    logger.error(f"Scale: failed to create container: {e}")
                    break

            return {
                "action": "scale_up",
                "previous": current,
                "current": current + added,
                "added": added,
            }

        elif target < current:
            # Only remove idle containers
            removed = 0
            to_remove = current - target

            async with self._condition:
                while removed < to_remove and self.idle:
                    pc = self.idle.pop()
                    await self._destroy_container(pc)
                    removed += 1

            return {
                "action": "scale_down",
                "previous": current,
                "current": current - removed,
                "removed": removed,
            }

        return {"action": "no_change", "current": current}

    async def reload_packages(self, db: AsyncSession) -> dict[str, Any]:
        """Reinstall all approved packages in every idle container."""
        if not self.idle:
            return {"status": "no_idle_containers", "message": "No idle containers to reload"}

        success = 0
        failed = 0
        errors = []

        for pc in list(self.idle):
            try:
                container = await asyncio.to_thread(
                    self.client.containers.get, pc.name
                )
                await self._install_packages(container, db)
                success += 1
            except Exception as e:
                failed += 1
                errors.append(f"{pc.name}: {e}")

        return {
            "status": "completed",
            "idle_containers": len(self.idle),
            "success": success,
            "failed": failed,
            "errors": errors or None,
        }

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        idle_list = [
            {
                "name": pc.name,
                "executions": pc.executions,
                "age_seconds": int(time.time() - pc.created_at),
            }
            for pc in self.idle
        ]
        in_use_list = [
            {
                "name": pc.name,
                "executions": pc.executions,
                "age_seconds": int(time.time() - pc.created_at),
            }
            for pc in self.in_use.values()
        ]

        return {
            "idle": len(self.idle),
            "in_use": len(self.in_use),
            "total": len(self.idle) + len(self.in_use),
            "max_size": settings.pool_max_size,
            "min_idle": settings.pool_min_idle,
            "max_executions": settings.pool_max_executions,
            "idle_containers": idle_list,
            "in_use_containers": in_use_list,
        }

    async def shutdown(self):
        """Cancel background tasks (containers stay alive with unless-stopped)."""
        if self._replenish_task:
            self._replenish_task.cancel()
        if self._health_task:
            self._health_task.cancel()


# Module-level singleton
container_pool = ContainerPool()
