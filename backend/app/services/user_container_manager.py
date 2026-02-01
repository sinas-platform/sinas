"""User container management for isolated function execution."""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import uuid
import docker
from docker.models.containers import Container
from docker.errors import NotFound, APIError

from app.core.config import settings
from app.models import Function
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class UserContainerManager:
    """Manages long-lived Docker containers per user for function execution."""

    def __init__(self):
        self.client = docker.from_env()
        # Track user containers: {user_id: {"container": Container, "last_used": timestamp}}
        self.user_containers: Dict[str, Dict[str, Any]] = {}
        self.container_lock = asyncio.Lock()
        # Start cleanup task
        self._cleanup_task = None

    async def start_cleanup_task(self):
        """Start background task to cleanup idle containers."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_idle_containers())

    async def _cleanup_idle_containers(self):
        """Background task to stop idle containers."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes

                idle_timeout = getattr(settings, 'function_container_idle_timeout', 3600)  # 1 hour default
                cutoff_time = time.time() - idle_timeout

                async with self.container_lock:
                    to_remove = []
                    for user_id, info in self.user_containers.items():
                        if info['last_used'] < cutoff_time:
                            try:
                                container = info['container']
                                # Run blocking Docker operations in thread pool
                                await asyncio.to_thread(container.stop, timeout=10)
                                await asyncio.to_thread(container.remove)
                                to_remove.append(user_id)
                                logger.info(f"Cleaned up idle container for user {user_id}")
                            except Exception as e:
                                logger.error(f"Error cleaning up container for user {user_id}: {e}")

                    for user_id in to_remove:
                        del self.user_containers[user_id]

            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")

    async def get_or_create_container(
        self,
        user_id: str,
        db: AsyncSession
    ) -> Container:
        """Get existing container for user or create new one."""
        async with self.container_lock:
            # Check if container exists in our tracking dict
            if user_id in self.user_containers:
                container_info = self.user_containers[user_id]
                container = container_info['container']

                try:
                    # Reload container status (run in thread pool)
                    await asyncio.to_thread(container.reload)
                    if container.status == 'running':
                        # Update last used time
                        container_info['last_used'] = time.time()
                        return container
                    else:
                        # Container stopped, remove and recreate
                        try:
                            await asyncio.to_thread(container.remove)
                        except:
                            pass
                        del self.user_containers[user_id]
                except NotFound:
                    # Container no longer exists
                    del self.user_containers[user_id]

            # Check if a container with this name already exists in Docker
            # (can happen after app restart when dict is cleared but containers still running)
            container_name = f'sinas-user-{user_id}'
            try:
                # Run blocking Docker call in thread pool
                existing = await asyncio.to_thread(self.client.containers.get, container_name)
                if existing.status == 'running':
                    logger.info(f"Found existing running container for user {user_id}, reusing it")
                    # Re-add to tracking dict
                    self.user_containers[user_id] = {
                        'container': existing,
                        'last_used': time.time()
                    }
                    return existing
                else:
                    # Container exists but not running, remove it
                    logger.info(f"Found stopped container for user {user_id}, removing it")
                    # Run blocking remove in thread pool
                    await asyncio.to_thread(existing.remove, force=True)
            except NotFound:
                # No existing container, proceed to create
                pass

            # Create new container
            container = await self._create_container(user_id, db)
            self.user_containers[user_id] = {
                'container': container,
                'last_used': time.time()
            }
            return container

    async def _create_container(self, user_id: str, db: AsyncSession) -> Container:
        """Create a new container for user with functions loaded."""
        logger.info(f"Creating new container for user {user_id}")

        # Get container image from settings (default to sinas-executor - minimal image with executor.py)
        image = getattr(settings, 'function_container_image', 'sinas-executor')

        # Create container with executor as main process (run in thread pool to avoid blocking)
        container = await asyncio.to_thread(
            self.client.containers.run,
            image,
            detach=True,
            name=f'sinas-user-{user_id}',
            mem_limit=f"{settings.max_function_memory}m",
            nano_cpus=int(settings.max_function_cpu * 1_000_000_000),  # Convert cores to nanocpus
            storage_opt={'size': settings.max_function_storage},
            network_mode='bridge',
            cap_drop=['ALL'],  # Drop all capabilities for security
            cap_add=['CHOWN', 'SETUID', 'SETGID'],  # Only essential capabilities
            security_opt=['no-new-privileges:true'],  # Prevent privilege escalation
            read_only=False,  # Allow writes to /tmp (needed for Python)
            tmpfs={'/tmp': 'size=100m,mode=1777'},  # Temp storage
            environment={
                'PYTHONUNBUFFERED': '1',
                'USER_ID': user_id,
            },
            labels={
                'sinas.user_id': user_id,
                'sinas.type': 'function-executor',
            },
            stdin_open=True,
            tty=False,
        )

        # Wait for container to be ready
        await asyncio.sleep(1)

        # Install required packages
        # Note: Functions are loaded inline at execution time, not pre-loaded
        await self._install_packages(container, user_id, db)

        logger.info(f"Container created for user {user_id}: {container.id[:12]}")
        return container

    async def _install_packages(self, container: Container, user_id: str, db: AsyncSession):
        """
        Install packages required by user's accessible functions.

        Only installs packages that are:
        1. Listed in function requirements
        2. Approved by admins (in InstalledPackage table)
        """
        from app.models.user import UserRole
        from app.models.package import InstalledPackage

        try:
            # Get user's groups
            groups_result = await db.execute(
                select(UserRole.role_id).where(UserRole.user_id == user_id)
            )
            group_ids = [row[0] for row in groups_result.all()]

            # Get all functions user has access to
            functions_result = await db.execute(
                select(Function).where(
                    Function.is_active == True,
                    (Function.user_id == user_id) | (Function.group_id.in_(group_ids))
                )
            )
            functions = functions_result.scalars().all()

            # Collect all requirements from accessible functions
            all_requirements = set()
            for func in functions:
                if func.requirements:
                    all_requirements.update(func.requirements)

            if not all_requirements:
                logger.info(f"No package requirements for user {user_id}")
                return

            # Get approved packages with versions
            approved_result = await db.execute(select(InstalledPackage))
            approved_packages = {pkg.package_name: pkg.version for pkg in approved_result.scalars().all()}

            # Filter requirements to only approved packages
            packages_to_install = []
            for req in all_requirements:
                # Extract package name from requirement spec
                package_name = req.split("==")[0].split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].strip()

                if package_name in approved_packages:
                    # Use admin-locked version if available, otherwise use requirement as-is
                    admin_version = approved_packages[package_name]
                    if admin_version:
                        packages_to_install.append(f"{package_name}=={admin_version}")
                    else:
                        packages_to_install.append(req)

            if not packages_to_install:
                logger.info(f"No approved packages to install for user {user_id}")
                return

            logger.info(f"Installing {len(packages_to_install)} packages in container for user {user_id}: {', '.join(packages_to_install)}")

            # Install packages in container
            install_cmd = ["pip", "install", "--no-cache-dir"] + packages_to_install

            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=install_cmd,
                demux=True,
            )

            stdout, stderr = exec_result.output
            if exec_result.exit_code == 0:
                logger.info(f"Successfully installed packages in container for user {user_id}")
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.warning(f"Package installation had issues for user {user_id}: {error_msg}")
                # Don't fail container creation - functions will get ImportError at runtime if needed

        except Exception as e:
            logger.error(f"Error installing packages in container for user {user_id}: {e}")
            # Don't fail container creation - log and continue

    async def _sync_functions(self, container: Container, user_id: str, db: AsyncSession):
        """Load all functions user has access to into container namespace, organized by namespace."""
        from app.models.user import UserRole

        # Get user's groups
        groups_result = await db.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        )
        group_ids = [row[0] for row in groups_result.all()]

        # Load functions from user's groups + user's own functions
        result = await db.execute(
            select(Function).where(
                Function.is_active == True,
                (Function.user_id == user_id) | (Function.group_id.in_(group_ids))
            )
        )
        functions = result.scalars().all()

        # Build functions payload organized by namespace
        # Format: {namespace: {function_name: {code, input_schema, output_schema, enabled_namespaces}}}
        functions_data = {}
        for func in functions:
            if func.namespace not in functions_data:
                functions_data[func.namespace] = {}

            functions_data[func.namespace][func.name] = {
                'code': func.code,
                'input_schema': func.input_schema,
                'output_schema': func.output_schema,
                'enabled_namespaces': func.enabled_namespaces or [],
            }

        # Send functions to container via the same trigger mechanism as execution
        payload = {
            'action': 'load_functions',
            'functions': functions_data,
        }

        # Write to container and trigger the executor to process it
        try:
            # Run blocking Docker exec in thread pool
            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=['python3', '-c', f'''
import sys
import json
import time
payload = {json.dumps(payload)}
# Write execution request
with open("/tmp/exec_request.json", "w") as f:
    json.dump(payload, f)
# Trigger execution
with open("/tmp/exec_trigger", "w") as f:
    f.write("1")
# Wait for result
max_wait = 10
start = time.time()
while time.time() - start < max_wait:
    try:
        with open("/tmp/exec_result.json", "r") as f:
            result = json.load(f)
            # Clear files
            import os
            os.remove("/tmp/exec_result.json")
            os.remove("/tmp/exec_trigger")
            os.remove("/tmp/exec_request.json")
            print(json.dumps(result))
            sys.exit(0)
    except FileNotFoundError:
        time.sleep(0.1)
        continue
print(json.dumps({{"error": "Function load timeout"}}))
sys.exit(1)
'''],
                demux=True,
            )

            stdout, stderr = exec_result.output
            logger.debug(f"Function sync exec result: exit_code={exec_result.exit_code}, stdout={stdout}, stderr={stderr}")

            if exec_result.exit_code == 0:
                if stdout:
                    try:
                        result = json.loads(stdout.decode())
                        total_functions = sum(len(funcs) for funcs in functions_data.values())
                        logger.info(f"Loaded {total_functions} functions across {len(functions_data)} namespaces into container for user {user_id}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Error parsing function sync result: {e}, stdout={stdout}")
                else:
                    # Exit code 0 but no stdout - functions might have loaded successfully
                    # Check stderr for any info
                    total_functions = sum(len(funcs) for funcs in functions_data.values())
                    logger.info(f"Function sync completed (no output captured), attempted to load {total_functions} functions")
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Error syncing functions to container: {error_msg}")
        except Exception as e:
            import traceback
            logger.error(f"Error syncing functions to container: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

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
        """Execute a function in user's container with inline code (no pre-loading needed)."""
        container = await self.get_or_create_container(user_id, db)

        # Update last used time
        async with self.container_lock:
            if user_id in self.user_containers:
                self.user_containers[user_id]['last_used'] = time.time()

        # Fetch function code from database (fresh every time)
        from app.models.function import Function
        result = await db.execute(
            select(Function).where(
                Function.namespace == function_namespace,
                Function.name == function_name,
                Function.is_active == True
            )
        )
        function = result.scalar_one_or_none()

        if not function:
            return {
                "status": "failed",
                "error": f"Function {function_namespace}/{function_name} not found"
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

        try:
            # Execute via exec_run (run in thread pool to avoid blocking)
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
'''],
                demux=True,
            )

            stdout, stderr = exec_result.output

            if exec_result.exit_code != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise Exception(f"Execution failed: {error_msg}")

            stdout_str = stdout.decode() if stdout else ""
            result = json.loads(stdout_str)

            if 'error' in result:
                raise Exception(result['error'])

            return result

        except Exception as e:
            logger.error(f"Error executing function in container: {e}")
            raise

    async def reload_all_containers(self):
        """Stop all user containers. They will be recreated on next execution with fresh packages."""
        async with self.container_lock:
            user_ids = list(self.user_containers.keys())
            stopped_count = 0

            for user_id in user_ids:
                try:
                    container = self.user_containers[user_id]['container']
                    await asyncio.to_thread(container.stop, timeout=10)
                    await asyncio.to_thread(container.remove)
                    del self.user_containers[user_id]
                    stopped_count += 1
                    logger.info(f"Stopped and removed container for user {user_id}")
                except Exception as e:
                    logger.error(f"Error stopping container for user {user_id}: {e}")

            logger.info(f"Reloaded all containers: stopped {stopped_count} containers")
            return {"stopped": stopped_count, "total": len(user_ids)}

    async def stop_container(self, user_id: str):
        """Stop and remove container for user."""
        async with self.container_lock:
            if user_id in self.user_containers:
                try:
                    container = self.user_containers[user_id]['container']
                    container.stop(timeout=10)
                    container.remove()
                    logger.info(f"Stopped container for user {user_id}")
                except Exception as e:
                    logger.error(f"Error stopping container: {e}")
                finally:
                    del self.user_containers[user_id]

    async def get_container_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all managed containers."""
        stats = []
        async with self.container_lock:
            for user_id, info in self.user_containers.items():
                try:
                    container = info['container']
                    await asyncio.to_thread(container.reload)
                    stats.append({
                        'user_id': user_id,
                        'container_id': container.id[:12],
                        'status': container.status,
                        'last_used': datetime.fromtimestamp(info['last_used']).isoformat(),
                        'idle_seconds': int(time.time() - info['last_used']),
                    })
                except Exception as e:
                    logger.error(f"Error getting stats for container: {e}")
        return stats


# Global instance
container_manager = UserContainerManager()
