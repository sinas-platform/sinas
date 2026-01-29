"""Shared worker pool manager for executing trusted functions."""
import asyncio
import docker
import json
import time
import traceback
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings


class SharedWorkerManager:
    """
    Manages a pool of shared worker containers for executing trusted functions.

    Unlike user containers (isolated per-user), workers are shared across all users
    for functions with shared_pool=True.

    Workers can be scaled up/down at runtime via API.
    """

    def __init__(self):
        self.client = docker.from_env()
        self.workers: Dict[str, Dict[str, Any]] = {}  # worker_id -> worker_info
        self.next_worker_index = 0  # For round-robin load balancing
        self._lock = asyncio.Lock()
        self._initialized = False
        self.docker_network = self._detect_network()

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
            networks = list(container.attrs['NetworkSettings']['Networks'].keys())
            if networks:
                detected = networks[0]
                print(f"🔍 Auto-detected Docker network: {detected}")
                return detected
        except Exception as e:
            print(f"⚠️  Failed to auto-detect network: {e}")

        # Fallback to common default
        print("⚠️  Using fallback network: bridge")
        return "bridge"

    async def initialize(self):
        """
        Initialize worker manager on startup.
        Re-discovers existing worker containers and scales to default count.
        """
        if self._initialized:
            return

        # Re-discover existing worker containers
        await self._discover_existing_workers()

        # Scale to default count if needed (get db session)
        current_count = len(self.workers)
        if current_count < settings.default_worker_count:
            print(f"📦 Scaling to default worker count: {settings.default_worker_count}")
            from app.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await self.scale_workers(settings.default_worker_count, db)

        self._initialized = True
        print(f"✅ Worker manager initialized with {len(self.workers)} workers")

    async def _discover_existing_workers(self):
        """Discover and re-register existing worker containers."""
        try:
            # List all containers with sinas-worker-* naming pattern
            containers = self.client.containers.list(
                filters={"name": "sinas-worker-"}
            )

            for container in containers:
                container_name = container.name
                # Extract worker number from name (sinas-worker-1 -> 1)
                if container_name.startswith("sinas-worker-"):
                    try:
                        worker_num = container_name.replace("sinas-worker-", "")
                        worker_id = f"worker-{worker_num}"

                        # Get container creation time
                        container_info = container.attrs
                        created_at = container_info.get("Created", datetime.utcnow().isoformat())

                        self.workers[worker_id] = {
                            "container_name": container_name,
                            "container_id": container.id,
                            "created_at": created_at,
                            "executions": 0,  # Reset execution count on rediscovery
                        }

                        print(f"🔍 Rediscovered worker: {container_name} (status: {container.status})")
                    except Exception as e:
                        print(f"⚠️  Failed to parse worker name {container_name}: {e}")

        except Exception as e:
            print(f"⚠️  Failed to discover existing workers: {e}")

    def get_worker_count(self) -> int:
        """Get current number of workers."""
        return len(self.workers)

    def list_workers(self) -> List[Dict[str, Any]]:
        """List all workers with status."""
        workers = []
        for worker_id, info in self.workers.items():
            try:
                container = self.client.containers.get(info["container_name"])
                workers.append({
                    "id": worker_id,
                    "container_name": info["container_name"],
                    "status": container.status,
                    "created_at": info["created_at"],
                    "executions": info.get("executions", 0),
                })
            except docker.errors.NotFound:
                # Container was removed
                workers.append({
                    "id": worker_id,
                    "container_name": info["container_name"],
                    "status": "missing",
                    "created_at": info["created_at"],
                    "executions": info.get("executions", 0),
                })
        return workers

    async def scale_workers(self, target_count: int, db: AsyncSession) -> Dict[str, Any]:
        """
        Scale workers to target count.

        Returns:
            Dict with scaling results
        """
        async with self._lock:
            current_count = len(self.workers)

            if target_count > current_count:
                # Scale up
                added = 0
                for _ in range(target_count - current_count):
                    worker_id = await self._create_worker(db)
                    if worker_id:
                        added += 1

                return {
                    "action": "scale_up",
                    "previous_count": current_count,
                    "current_count": len(self.workers),
                    "added": added
                }

            elif target_count < current_count:
                # Scale down
                removed = 0
                workers_to_remove = list(self.workers.keys())[target_count:]

                for worker_id in workers_to_remove:
                    if await self._remove_worker(worker_id):
                        removed += 1

                return {
                    "action": "scale_down",
                    "previous_count": current_count,
                    "current_count": len(self.workers),
                    "removed": removed
                }

            else:
                return {
                    "action": "no_change",
                    "current_count": current_count
                }

    async def _create_worker(self, db: AsyncSession) -> Optional[str]:
        """Create a new worker container."""
        worker_id = f"worker-{len(self.workers) + 1}"
        container_name = f"sinas-worker-{len(self.workers) + 1}"

        try:
            # Create worker container (same security model as user containers)
            container = self.client.containers.run(
                image=settings.function_container_image,  # sinas-executor
                name=container_name,
                detach=True,
                network=self.docker_network,
                mem_limit="1g",
                nano_cpus=1_000_000_000,  # 1 CPU core
                cap_drop=['ALL'],  # Drop all capabilities for security
                cap_add=['CHOWN', 'SETUID', 'SETGID'],  # Only essential capabilities
                security_opt=['no-new-privileges:true'],  # Prevent privilege escalation
                tmpfs={'/tmp': 'size=100m,mode=1777'},  # Temp storage only
                environment={
                    'PYTHONUNBUFFERED': '1',
                    "WORKER_MODE": "true",
                    "WORKER_ID": worker_id,
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
        from app.models.package import InstalledPackage

        try:
            # Get all approved packages
            result = await db.execute(select(InstalledPackage))
            approved_packages = result.scalars().all()

            if not approved_packages:
                print(f"📦 No approved packages to install in worker")
                return

            # Build package specs with admin-locked versions
            packages_to_install = []
            for pkg in approved_packages:
                if pkg.version:
                    packages_to_install.append(f"{pkg.package_name}=={pkg.version}")
                else:
                    packages_to_install.append(pkg.package_name)

            print(f"📦 Installing {len(packages_to_install)} packages in worker: {', '.join(packages_to_install)}")

            # Install packages in container
            install_cmd = ["pip", "install", "--no-cache-dir"] + packages_to_install

            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=install_cmd,
                demux=True,
            )

            stdout, stderr = exec_result.output
            if exec_result.exit_code == 0:
                print(f"✅ Successfully installed packages in worker")
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                print(f"⚠️  Package installation had issues in worker: {error_msg}")
                # Don't fail worker creation - log and continue

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

    async def execute_function(
        self,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        enabled_namespaces: List[str],
        input_data: Dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: Optional[str],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Execute function in a worker container using round-robin load balancing.
        """
        async with self._lock:
            if not self.workers:
                return {
                    "status": "failed",
                    "error": "No workers available. Please scale workers up first."
                }

            # Round-robin load balancing
            worker_ids = list(self.workers.keys())
            worker_id = worker_ids[self.next_worker_index % len(worker_ids)]
            self.next_worker_index += 1

            worker_info = self.workers[worker_id]
            container_name = worker_info["container_name"]

        try:
            container = self.client.containers.get(container_name)

            # Fetch function code from database
            from app.models.function import Function
            result = await db.execute(
                select(Function).where(
                    Function.namespace == function_namespace,
                    Function.name == function_name,
                    Function.is_active == True,
                    Function.shared_pool == True
                )
            )
            function = result.scalar_one_or_none()

            if not function:
                return {
                    "status": "failed",
                    "error": f"Function {function_namespace}/{function_name} not found or not marked as shared_pool"
                }

            # Prepare execution payload with inline code
            payload = {
                'action': 'execute_inline',
                'function_code': function.code,
                'execution_id': execution_id,
                'function_namespace': function_namespace,
                'function_name': function_name,
                'enabled_namespaces': enabled_namespaces,
                'input_data': input_data,
                'context': {
                    'user_id': user_id,
                    'user_email': user_email,
                    'access_token': access_token,
                    'execution_id': execution_id,
                    'trigger_type': trigger_type,
                    'chat_id': chat_id,
                }
            }

            # Execute via file-based trigger (run in thread pool to avoid blocking)
            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=['python3', '-c', f'''
import sys
import json
payload = {json.dumps(payload)}
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
'''])

            output = exec_result.output.decode()

            # Parse result
            if exec_result.exit_code == 0:
                result = json.loads(output)

                # Track execution count
                async with self._lock:
                    self.workers[worker_id]["executions"] = self.workers[worker_id].get("executions", 0) + 1

                return result
            else:
                return {
                    "status": "failed",
                    "error": output
                }

        except Exception as e:
            return {
                "status": "failed",
                "error": f"Worker execution failed: {str(e)}"
            }


# Global worker manager instance
shared_worker_manager = SharedWorkerManager()
