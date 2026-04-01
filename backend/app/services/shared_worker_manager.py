"""Shared worker pool manager for executing trusted functions."""
import asyncio
import io
import json
import tarfile
from datetime import datetime
from typing import Any, Optional

import docker
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

WORKER_EXEC_COUNT_KEY = "sinas:worker:executions"


class SharedWorkerManager:
    """
    Manages a pool of shared worker containers for executing trusted functions.

    Unlike user containers (isolated per-user), workers are shared across all users
    for functions with shared_pool=True.

    Workers can be scaled up/down at runtime via API.
    """

    def __init__(self):
        self.client = docker.from_env()
        self.workers: dict[str, dict[str, Any]] = {}  # worker_id -> worker_info
        self.next_worker_index = 0  # For round-robin load balancing
        self._lock = asyncio.Lock()
        self._initialized = False
        self.docker_network = self._detect_network()
        self.sandbox_network = self._ensure_sandbox_network()

    def _detect_network(self) -> str:
        """Auto-detect Docker network if set to 'auto', otherwise use configured value."""
        network = settings.docker_network

        if network != "auto":
            return network

        # Try to auto-detect by inspecting the backend container
        try:
            import socket

            hostname = socket.gethostname()
            container = self.client.containers.get(hostname)
            networks = list(container.attrs["NetworkSettings"]["Networks"].keys())
            if networks:
                detected = networks[0]
                print(f"🔍 Auto-detected Docker network: {detected}")
                return detected
        except Exception as e:
            print(f"⚠️  Failed to auto-detect network: {e}")

        # Fallback to common default
        print("⚠️  Using fallback network: bridge")
        return "bridge"

    def _ensure_sandbox_network(self) -> str:
        """Ensure the isolated sandbox network exists for executor containers.

        This network allows internet access but is completely separate from
        the internal network where Redis, Postgres, etc. live.
        """
        from docker.errors import NotFound

        network_name = settings.sandbox_network
        try:
            self.client.networks.get(network_name)
        except NotFound:
            print(f"🔒 Creating sandbox network: {network_name}")
            self.client.networks.create(
                network_name,
                driver="bridge",
                labels={"sinas.type": "sandbox"},
            )
        return network_name

    async def initialize(self):
        """
        Initialize worker manager on startup.
        Removes stale workers (wrong image), cleans up stuck executions,
        and scales to default count.

        Only called by the backend process (main.py), not by queue workers.
        """
        if self._initialized:
            return

        from app.core.database import AsyncSessionLocal

        # Remove old workers running a stale executor image
        await self._remove_stale_workers()

        # Re-discover any remaining valid worker containers.
        # restart=True ensures a fresh executor and correct network after
        # compose down/up.  Safe here because the scheduler is a single replica.
        # Pass db so packages are reinstalled on discovered workers.
        async with AsyncSessionLocal() as db:
            await self._discover_existing_workers(restart=True, db=db)

            # Clean up executions stuck in "running" state from previous lifecycle
            await self._cleanup_stuck_executions(db)

        # Scale to default count if needed
        current_count = len(self.workers)
        if current_count < settings.default_worker_count:
            print(f"📦 Scaling to default worker count: {settings.default_worker_count}")
            async with AsyncSessionLocal() as db:
                await self.scale_workers(settings.default_worker_count, db)

        self._initialized = True
        print(f"✅ Worker manager initialized with {len(self.workers)} workers")

    async def _discover_existing_workers(
        self, restart: bool = False, db: Optional[AsyncSession] = None
    ):
        """Discover and re-register existing worker containers (including stopped ones).

        Args:
            restart: When True (scheduler only), restart all containers to
                ensure a fresh executor process, clean tmpfs, and correct
                network connectivity.  Queue workers pass False (default)
                to avoid racing with the scheduler or each other.
            db: Database session for reinstalling packages on discovered workers.
        """
        try:
            # List all containers (including stopped) with sinas-shared-* naming pattern
            containers = self.client.containers.list(all=True, filters={"name": "sinas-shared-"})

            # Filter to valid worker containers
            valid: list[tuple[str, str, Any]] = []  # (worker_id, name, container)
            for container in containers:
                container_name = container.name
                if container_name.startswith("sinas-shared-"):
                    worker_num = container_name.replace("sinas-shared-", "")
                    worker_id = f"worker-{worker_num}"
                    valid.append((worker_id, container_name, container))

            async def _process_worker(
                worker_id: str, container_name: str, container
            ) -> Optional[dict]:
                """Process a single worker; returns worker info dict or None."""
                if restart:
                    try:
                        print(f"🔄 Restarting worker: {container_name}")
                        await asyncio.to_thread(container.restart, timeout=10)
                        await asyncio.to_thread(container.reload)
                    except docker.errors.APIError as restart_err:
                        print(
                            f"⚠️  Cannot restart {container_name}, removing: {restart_err}"
                        )
                        try:
                            await asyncio.to_thread(container.remove, force=True)
                        except Exception:
                            pass
                        return None

                    # Ensure container is on the sandbox network
                    try:
                        current_networks = set(
                            container.attrs.get("NetworkSettings", {})
                            .get("Networks", {})
                            .keys()
                        )
                        if self.sandbox_network not in current_networks:
                            print(
                                f"🔗 Reconnecting {container_name} to "
                                f"sandbox network {self.sandbox_network}"
                            )
                            network = await asyncio.to_thread(
                                self.client.networks.get, self.sandbox_network
                            )
                            await asyncio.to_thread(network.connect, container)
                    except Exception as net_err:
                        print(
                            f"⚠️  Failed to reconnect {container_name} "
                            f"to network: {net_err}"
                        )
                else:
                    # Queue-worker path: only start stopped containers
                    if container.status != "running":
                        print(f"🔄 Starting stopped worker: {container_name}")
                        try:
                            await asyncio.to_thread(container.start)
                            await asyncio.to_thread(container.reload)
                        except docker.errors.APIError as start_err:
                            print(
                                f"⚠️  Cannot start {container_name}, removing: {start_err}"
                            )
                            try:
                                await asyncio.to_thread(container.remove, force=True)
                            except Exception:
                                pass
                            return None

                # Reinstall packages to ensure discovered workers are up-to-date
                if db:
                    print(f"📦 Installing packages in discovered worker: {container_name}")
                    await self._install_packages(container, db)

                created_at = container.attrs.get("Created", datetime.utcnow().isoformat())
                print(
                    f"🔍 Rediscovered worker: {container_name} (status: {container.status})"
                )
                return {
                    "worker_id": worker_id,
                    "container_name": container_name,
                    "container_id": container.id,
                    "created_at": created_at,
                    "executions": 0,
                }

            # Process all workers in parallel
            results = await asyncio.gather(
                *[_process_worker(wid, name, c) for wid, name, c in valid],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, dict):
                    self.workers[r["worker_id"]] = {
                        "container_name": r["container_name"],
                        "container_id": r["container_id"],
                        "created_at": r["created_at"],
                        "executions": r["executions"],
                    }

        except Exception as e:
            print(f"⚠️  Failed to discover existing workers: {e}")

    async def _remove_stale_workers(self):
        """Remove worker containers running an outdated executor image."""
        try:
            current_image = self.client.images.get(settings.function_container_image)
            current_image_id = current_image.id
        except Exception as e:
            print(f"⚠️  Cannot resolve current executor image: {e}")
            return

        try:
            containers = self.client.containers.list(
                all=True, filters={"name": "sinas-shared-"}
            )
            removed = 0
            for container in containers:
                if not container.name.startswith("sinas-shared-"):
                    continue
                try:
                    container_image_id = container.image.id
                except Exception:
                    # Old image was pruned — treat as stale
                    container_image_id = None
                if container_image_id != current_image_id:
                    print(
                        f"🗑️  Removing stale worker {container.name} (old image)"
                    )
                    try:
                        container.remove(force=True)
                        removed += 1
                    except Exception as e:
                        print(f"⚠️  Failed to remove {container.name}: {e}")
            if removed:
                print(f"🗑️  Removed {removed} stale worker(s)")
        except Exception as e:
            print(f"⚠️  Failed to clean stale workers: {e}")

    async def _cleanup_stuck_executions(self, db: AsyncSession):
        """Mark executions stuck in 'running' or 'awaiting_input' state as failed after restart."""
        try:
            from app.models.execution import Execution, ExecutionStatus

            result = await db.execute(
                update(Execution)
                .where(Execution.status.in_([ExecutionStatus.RUNNING, ExecutionStatus.AWAITING_INPUT]))
                .values(
                    status=ExecutionStatus.FAILED,
                    error="Execution interrupted by server restart",
                    completed_at=datetime.utcnow(),
                    container_id=None,
                )
            )
            if result.rowcount > 0:
                print(f"🧹 Marked {result.rowcount} stuck execution(s) as failed")
            await db.commit()
        except Exception as e:
            print(f"⚠️  Failed to clean stuck executions: {e}")

    def get_worker_count(self) -> int:
        """Get current number of workers by querying Docker directly."""
        try:
            containers = self.client.containers.list(
                all=True, filters={"name": "sinas-shared-"}
            )
            return sum(
                1 for c in containers if c.name.startswith("sinas-shared-")
            )
        except Exception:
            return len(self.workers)

    async def list_workers(self) -> list[dict[str, Any]]:
        """List all workers by querying Docker directly, with execution counts from Redis."""
        # Read execution counts from Redis (shared across processes)
        exec_counts: dict[str, int] = {}
        try:
            from redis.asyncio import Redis

            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            raw = await redis.hgetall(WORKER_EXEC_COUNT_KEY)
            exec_counts = {k: int(v) for k, v in raw.items()}
            await redis.aclose()
        except Exception:
            pass

        workers = []
        try:
            containers = await asyncio.to_thread(
                self.client.containers.list,
                all=True,
                filters={"name": "sinas-shared-"},
            )
            for container in containers:
                if not container.name.startswith("sinas-shared-"):
                    continue
                worker_num = container.name.replace("sinas-shared-", "")
                worker_id = f"worker-{worker_num}"
                created_at = container.attrs.get("Created", "")
                workers.append(
                    {
                        "id": worker_id,
                        "container_name": container.name,
                        "status": container.status,
                        "created_at": created_at,
                        "executions": exec_counts.get(worker_id, 0),
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to list worker containers from Docker: {e}")

        return workers

    async def scale_workers(self, target_count: int, db: AsyncSession) -> dict[str, Any]:
        """
        Scale workers to target count.

        Returns:
            Dict with scaling results
        """
        async with self._lock:
            current_count = len(self.workers)

            if target_count > current_count:
                # Scale up — pre-assign worker numbers then create in parallel
                nums = []
                for _ in range(target_count - current_count):
                    num = self._next_worker_number()
                    wid = f"worker-{num}"
                    # Reserve the slot so _next_worker_number skips it
                    self.workers[wid] = {"container_name": f"sinas-shared-{num}", "container_id": "", "created_at": "", "executions": 0}
                    nums.append(num)

                results = await asyncio.gather(
                    *[self._create_worker_num(num, db) for num in nums],
                    return_exceptions=True,
                )
                # Remove reservations that failed
                for num, r in zip(nums, results):
                    wid = f"worker-{num}"
                    if not isinstance(r, str):
                        self.workers.pop(wid, None)
                added = sum(1 for r in results if isinstance(r, str))

                return {
                    "action": "scale_up",
                    "previous_count": current_count,
                    "current_count": len(self.workers),
                    "added": added,
                }

            elif target_count < current_count:
                # Scale down — skip workers with paused executions
                removed = 0
                workers_to_remove = list(self.workers.keys())[target_count:]

                # Check for AWAITING_INPUT executions on each worker
                paused_containers: set[str] = set()
                try:
                    from app.core.database import AsyncSessionLocal
                    from app.models.execution import Execution, ExecutionStatus

                    async with AsyncSessionLocal() as check_db:
                        result = await check_db.execute(
                            select(Execution.container_id).where(
                                Execution.status == ExecutionStatus.AWAITING_INPUT,
                                Execution.container_id.isnot(None),
                            )
                        )
                        paused_containers = {row[0] for row in result.all()}
                except Exception as e:
                    print(f"⚠️  Could not check paused executions: {e}")

                for worker_id in workers_to_remove:
                    info = self.workers.get(worker_id, {})
                    container_name = info.get("container_name", "")
                    if container_name in paused_containers:
                        print(f"⏸️  Skipping removal of {container_name} — has paused executions")
                        continue
                    if await self._remove_worker(worker_id):
                        removed += 1

                return {
                    "action": "scale_down",
                    "previous_count": current_count,
                    "current_count": len(self.workers),
                    "removed": removed,
                }

            else:
                return {"action": "no_change", "current_count": current_count}

    def _next_worker_number(self) -> int:
        """Find the next available worker number that doesn't collide with existing workers."""
        existing_numbers = set()
        for wid in self.workers:
            # worker IDs are "worker-N"
            try:
                existing_numbers.add(int(wid.split("-", 1)[1]))
            except (IndexError, ValueError):
                pass
        n = 1
        while n in existing_numbers:
            n += 1
        return n

    async def _create_worker(self, db: AsyncSession) -> Optional[str]:
        """Create a new worker container (auto-assigns number)."""
        num = self._next_worker_number()
        return await self._create_worker_num(num, db)

    async def _create_worker_num(self, num: int, db: AsyncSession) -> Optional[str]:
        """Create a new worker container with an explicit worker number."""
        worker_id = f"worker-{num}"
        container_name = f"sinas-shared-{num}"

        try:
            # Remove stale container with same name if it exists (e.g. after crash)
            try:
                stale = await asyncio.to_thread(self.client.containers.get, container_name)
                print(f"🗑️  Removing stale container: {container_name}")
                await asyncio.to_thread(stale.remove, force=True)
            except docker.errors.NotFound:
                pass

            # Create worker container (same security model as user containers)
            container = await asyncio.to_thread(
                self.client.containers.run,
                image=settings.function_container_image,  # sinas-executor
                name=container_name,
                detach=True,
                network=self.sandbox_network,
                mem_limit="1g",
                nano_cpus=1_000_000_000,  # 1 CPU core
                cap_drop=["ALL"],  # Drop all capabilities for security
                cap_add=["CHOWN", "SETUID", "SETGID"],  # Only essential capabilities
                security_opt=["no-new-privileges:true"],  # Prevent privilege escalation
                pids_limit=256,  # Prevent fork bombs
                extra_hosts={"host.docker.internal": "host-gateway"},
                tmpfs={"/tmp": "size=100m,mode=1777"},  # Temp storage only
                environment={
                    "PYTHONUNBUFFERED": "1",
                    "WORKER_MODE": "true",
                    "WORKER_ID": worker_id,
                    "SINAS_CONTAINER_MODE": "shared",
                },
                # Use default command from image (python3 -u /app/executor.py)
                # Don't override with custom command - executor is needed
                restart_policy={"Name": "unless-stopped"},
            )

            self.workers[worker_id] = {
                "container_name": container_name,
                "container_id": container.id,
                "created_at": datetime.utcnow().isoformat(),
                "executions": 0,
            }

            # Wait for container and executor to be ready
            await asyncio.sleep(2)

            # Install all approved packages in worker
            await self._install_packages(container, db)

            print(f"✅ Created worker: {container_name}")
            return worker_id

        except Exception as e:
            print(f"❌ Failed to create worker {container_name}: {e}")
            return None

    async def _install_packages(self, container, db: AsyncSession):
        """
        Install all approved packages in shared worker.

        Shared workers execute any trusted function, so they need all packages.
        """
        from app.models.dependency import Dependency

        try:
            # Get all approved packages
            result = await db.execute(select(Dependency))
            approved_packages = result.scalars().all()

            if not approved_packages:
                print("📦 No approved packages to install in worker")
                return

            # Build package specs with admin-locked versions
            packages_to_install = []
            for pkg in approved_packages:
                if pkg.version:
                    packages_to_install.append(f"{pkg.package_name}=={pkg.version}")
                else:
                    packages_to_install.append(pkg.package_name)

            print(
                f"📦 Installing {len(packages_to_install)} packages in worker: {', '.join(packages_to_install)}"
            )

            # Install packages in container
            install_cmd = ["pip", "install", "--no-cache-dir", "--upgrade"] + packages_to_install

            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=install_cmd,
                demux=True,
            )

            stdout, stderr = exec_result.output
            if exec_result.exit_code == 0:
                print("✅ Successfully installed packages in worker")
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                print(
                    f"❌ Package installation failed in worker "
                    f"(exit code {exec_result.exit_code}): {error_msg}"
                )

        except Exception as e:
            print(f"❌ Error installing packages in worker: {e}")
            # Don't fail worker creation - log and continue

    async def _remove_worker(self, worker_id: str) -> bool:
        """Remove a worker container."""
        if worker_id not in self.workers:
            return False

        info = self.workers[worker_id]
        container_name = info["container_name"]

        try:
            container = self.client.containers.get(container_name)
            container.stop(timeout=10)
            container.remove()

            del self.workers[worker_id]

            print(f"✅ Removed worker: {container_name}")
            return True

        except docker.errors.NotFound:
            # Already removed
            del self.workers[worker_id]
            return True
        except Exception as e:
            print(f"❌ Failed to remove worker {container_name}: {e}")
            return False

    async def reload_packages(self, db: AsyncSession) -> dict[str, Any]:
        """
        Reload packages in all shared workers.
        Reinstalls all approved packages and restarts each container
        so the executor process picks up the new modules.
        """
        async with self._lock:
            if not self.workers:
                return {"status": "no_workers", "message": "No workers to reload"}

            success_count = 0
            failed_count = 0
            errors = []

            for worker_id, info in self.workers.items():
                container_name = info["container_name"]
                try:
                    container = self.client.containers.get(container_name)
                    await self._install_packages(container, db)
                    # Restart container so the executor process reloads modules
                    await asyncio.to_thread(container.restart, timeout=10)
                    success_count += 1
                    print(f"✅ Reloaded packages in worker: {container_name}")
                except Exception as e:
                    failed_count += 1
                    error_msg = f"Worker {container_name}: {str(e)}"
                    errors.append(error_msg)
                    print(f"❌ Failed to reload packages in {container_name}: {e}")

            return {
                "status": "completed",
                "total_workers": len(self.workers),
                "success": success_count,
                "failed": failed_count,
                "errors": errors if errors else None,
            }

    async def _load_secrets(self, db, user_id: str = None) -> dict[str, str]:
        """Load secrets, decrypt, return as {name: value} dict.
        Private secrets override shared for the given user.
        """
        from sqlalchemy import and_
        from app.core.encryption import encryption_service
        from app.models.secret import Secret

        # Load shared secrets first
        result = await db.execute(select(Secret).where(Secret.visibility == "shared"))
        secrets = {}
        for secret in result.scalars().all():
            try:
                secrets[secret.name] = encryption_service.decrypt(secret.encrypted_value)
            except Exception:
                logger.warning(f"Failed to decrypt secret '{secret.name}', skipping")

        # Override with private secrets for this user
        if user_id:
            result = await db.execute(
                select(Secret).where(and_(Secret.user_id == user_id, Secret.visibility == "private"))
            )
            for secret in result.scalars().all():
                try:
                    secrets[secret.name] = encryption_service.decrypt(secret.encrypted_value)
                except Exception:
                    logger.warning(f"Failed to decrypt private secret '{secret.name}', skipping")

        return secrets

    async def execute_function(
        self,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: Optional[str],
        db: AsyncSession,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Execute function in a worker container using round-robin load balancing.

        Each execution uses unique file paths (/tmp/exec_{request,trigger,result}_{eid})
        so multiple executions can be in-flight on the same container concurrently.
        The container executor processes them one at a time but no requests are lost.
        """
        async with self._lock:
            if not self.workers:
                # Workers may have been created after this process started —
                # attempt re-discovery before giving up.
                await self._discover_existing_workers()
            if not self.workers:
                return {
                    "status": "failed",
                    "error": "No workers available. Please scale workers up first.",
                }

            # Round-robin load balancing
            worker_ids = list(self.workers.keys())
            worker_id = worker_ids[self.next_worker_index % len(worker_ids)]
            self.next_worker_index += 1

            worker_info = self.workers[worker_id]
            container_name = worker_info["container_name"]

        try:
            container = self.client.containers.get(container_name)

            # Verify container is actually running (not crashed/stopped)
            if container.status != "running":
                logger.warning(f"Worker {container_name} not running (status={container.status}), removing")
                async with self._lock:
                    self.workers.pop(worker_id, None)
                return {"status": "failed", "error": f"Worker {container_name} not running"}

            # Fetch function code from database
            from app.models.function import Function

            result = await db.execute(
                select(Function).where(
                    Function.namespace == function_namespace,
                    Function.name == function_name,
                    Function.is_active == True,
                    Function.shared_pool == True,
                )
            )
            function = result.scalar_one_or_none()

            if not function:
                return {
                    "status": "failed",
                    "error": f"Function {function_namespace}/{function_name} not found or not marked as shared_pool",
                }

            # Prepare execution payload with inline code
            effective_timeout = timeout or settings.function_timeout
            payload = {
                "action": "execute_inline",
                "function_code": function.code,
                "execution_id": execution_id,
                "function_namespace": function_namespace,
                "function_name": function_name,
                "timeout": effective_timeout,
                "input_data": input_data,
                "context": {
                    "user_id": user_id,
                    "user_email": user_email,
                    "access_token": access_token,
                    "execution_id": execution_id,
                    "trigger_type": trigger_type,
                    "chat_id": chat_id,
                    "secrets": await self._load_secrets(db, user_id),
                },
            }

            # Per-execution file paths so concurrent requests don't collide
            eid = execution_id
            request_file = f"/tmp/exec_request_{eid}.json"
            trigger_file = f"/tmp/exec_trigger_{eid}"
            result_file = f"/tmp/exec_result_{eid}.json"

            # Write payload to container via exec_run + stdin pipe.
            # We cannot use put_archive: it writes to the overlay layer which
            # is invisible through tmpfs mounts on Linux.
            # Stdin piping has no ARG_MAX limit and works with any payload size.
            payload_bytes = json.dumps(payload).encode("utf-8")

            # Step 1: Pipe payload into container via stdin (exec_create + exec_start)
            api = container.client.api
            exec_id = api.exec_create(
                container.id,
                ['python3', '-c', f'import sys; open("{request_file}","wb").write(sys.stdin.buffer.read())'],
                stdin=True,
                stdout=True,
                stderr=True,
            )["Id"]
            sock = api.exec_start(exec_id, socket=True)
            sock._sock.sendall(payload_bytes)
            import socket as _sock_mod
            sock._sock.shutdown(_sock_mod.SHUT_WR)
            sock.read()  # Wait for command to finish
            sock.close()

            # Step 2: Trigger execution and poll for result
            # The polling script recognizes "awaiting_input" as a valid intermediate
            # result and returns immediately so the backend can handle pause/resume.
            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=[
                    "python3",
                    "-c",
                    f"""
import sys, json, time, os
with open("{trigger_file}", "w") as f:
    f.write("1")
max_wait = {effective_timeout}
start = time.time()
while time.time() - start < max_wait:
    try:
        with open("{result_file}", "r") as f:
            result = json.load(f)
            if result.get("status") == "awaiting_input":
                # Return immediately — function is paused, don't keep polling
                print(json.dumps(result))
                sys.exit(0)
            # Final result (completed/failed)
            print(json.dumps(result))
            sys.exit(0)
    except FileNotFoundError:
        time.sleep(0.1)
        continue
print(json.dumps({{"error": "Execution timeout after {effective_timeout}s"}}))
sys.exit(1)
""",
                ],
                demux=True,
            )

            stdout, stderr = exec_result.output
            stdout_str = stdout.decode() if stdout else ""

            if exec_result.exit_code == 0:
                result = json.loads(stdout_str)

                # Include container_name so backend knows where to resume
                if result.get("status") == "awaiting_input":
                    result["container_name"] = container_name
                    return result

                # Track execution count in Redis (shared across processes)
                try:
                    from redis.asyncio import Redis

                    redis = Redis.from_url(settings.redis_url, decode_responses=True)
                    await redis.hincrby(WORKER_EXEC_COUNT_KEY, worker_id, 1)
                    await redis.aclose()
                except Exception:
                    pass  # Non-critical — don't fail execution over counter

                return result
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                return {"status": "failed", "error": error_msg}

        except Exception as e:
            return {"status": "failed", "error": f"Worker execution failed: {str(e)}"}


# Global worker manager instance
shared_worker_manager = SharedWorkerManager()
