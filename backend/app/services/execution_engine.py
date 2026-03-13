"""Function execution engine with tracking and validation."""
import asyncio
import json
import logging
import time
import traceback
from datetime import datetime
from typing import Any, Optional

import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.execution import Execution, ExecutionStatus
from app.models.function import Function
from app.services.clickhouse_logger import clickhouse_logger


logger = logging.getLogger(__name__)


class FunctionExecutionError(Exception):
    pass


class SchemaValidationError(Exception):
    pass


class FunctionExecutor:
    def __init__(self):
        self.functions_cache: dict[str, Function] = {}
        self._container_pool = None

    @property
    def container_pool(self):
        """Lazy load container pool (replaces per-user containers)."""
        if self._container_pool is None:
            from app.services.container_pool import container_pool

            self._container_pool = container_pool
        return self._container_pool

    @property
    def worker_manager(self):
        """Lazy load shared worker manager."""
        if not hasattr(self, "_worker_manager") or self._worker_manager is None:
            from app.services.shared_worker_manager import shared_worker_manager

            self._worker_manager = shared_worker_manager
        return self._worker_manager

    async def validate_schema(self, data: Any, schema: dict[str, Any]) -> Any:
        """
        Validate data against JSON schema with type coercion.

        Returns:
            Coerced data
        """
        from app.utils.schema import validate_with_coercion

        try:
            return validate_with_coercion(data, schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(f"Schema validation failed: {e.message}")

    async def _execute_in_shared_pool(
        self,
        function: Function,
        input_data: dict[str, Any],
        execution_id: str,
        user_id: str,
        user_email: str,
        access_token: str,
        trigger_type: str,
        chat_id: Optional[str],
        db: AsyncSession,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Execute function in shared worker pool (separate worker containers).

        SECURITY: Only use for trusted, admin-created functions.
        Workers are separate containers from backend but shared across users.
        No per-user isolation - functions must be trusted.
        """
        exec_result = await self.worker_manager.execute_function(
            user_id=user_id,
            user_email=user_email,
            access_token=access_token,
            function_namespace=function.namespace,
            function_name=function.name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            chat_id=chat_id,
            db=db,
            timeout=timeout,
        )

        return exec_result

    async def load_function(
        self, db: AsyncSession, function_namespace: str, function_name: str, user_id: str
    ) -> Function:
        """Load function from database with caching.

        Cache is invalidated when the function's updated_at changes,
        so code/config edits are picked up without process restart.

        Note: No permission checks here - permissions should be validated at entry points
        (agent tool execution, webhook validation, schedule authorization).
        """
        cache_key = f"{function_namespace}:{function_name}"

        result = await db.execute(
            select(Function).where(
                Function.namespace == function_namespace,
                Function.name == function_name,
                Function.is_active == True,
            )
        )
        function = result.scalar_one_or_none()

        if not function:
            raise FunctionExecutionError(
                f"Function '{function_namespace}/{function_name}' not found or inactive"
            )

        # Invalidate cache if function was updated since last load
        cached = self.functions_cache.get(cache_key)
        if cached and cached.updated_at != function.updated_at:
            logger.info(f"Function cache invalidated: {cache_key} (updated)")

        self.functions_cache[cache_key] = function
        return function

    async def execute_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a function with input validation and tracking."""
        async with AsyncSessionLocal() as db:
            # Get user info for context
            from app.core.auth import create_access_token
            from app.models.user import User

            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            user_email = user.email if user else "unknown@unknown.com"

            # Generate access token for function to make authenticated API calls
            access_token = create_access_token(user_id, user_email)

            # Check if execution already exists (e.g. created by enqueue_function)
            result = await db.execute(
                select(Execution).where(Execution.execution_id == execution_id)
            )
            execution = result.scalar_one_or_none()

            if not execution:
                # Create new execution record
                execution = Execution(
                    user_id=user_id,
                    execution_id=execution_id,
                    function_name=function_name,
                    trigger_type=trigger_type,
                    trigger_id=trigger_id,
                    chat_id=chat_id,
                    status=ExecutionStatus.RUNNING,
                    input_data=input_data,
                    started_at=datetime.utcnow(),
                )
                db.add(execution)
                await db.commit()

                # Log execution start to Redis
                await clickhouse_logger.log_execution_start(execution_id, function_name, input_data)

            try:
                # Load function definition
                function = await self.load_function(db, function_namespace, function_name, user_id)

                # Validate input
                if function.input_schema:
                    # Validate and coerce types (handles string -> number, etc.)
                    input_data = await self.validate_schema(input_data, function.input_schema)

                start_time = time.time()

                # Per-function timeout (falls back to global setting)
                function_timeout = function.timeout or settings.function_timeout

                # Route execution based on shared_pool setting
                if function.shared_pool:
                    # Execute in shared worker container pool
                    print(
                        f"⏱️  [TIMING] Executing {function_namespace}/{function_name} in shared pool"
                    )
                    exec_result = await self._execute_in_shared_pool(
                        function=function,
                        input_data=input_data,
                        execution_id=execution_id,
                        user_id=user_id,
                        user_email=user_email,
                        access_token=access_token,
                        trigger_type=trigger_type,
                        chat_id=chat_id,
                        db=db,
                        timeout=function_timeout,
                    )
                    elapsed = time.time() - start_time
                    print(f"⏱️  [TIMING] Shared pool execution completed in {elapsed:.3f}s")
                else:
                    # Execute in pooled Docker container (untrusted code)
                    print(
                        f"⏱️  [TIMING] Executing {function_namespace}/{function_name} in sandbox container"
                    )
                    container_start = time.time()
                    exec_result = await self.container_pool.execute_function(
                        user_id=user_id,
                        user_email=user_email,
                        access_token=access_token,
                        function_namespace=function_namespace,
                        function_name=function_name,
                        input_data=input_data,
                        execution_id=execution_id,
                        trigger_type=trigger_type,
                        chat_id=chat_id,
                        db=db,
                        timeout=function_timeout,
                    )
                    container_elapsed = time.time() - container_start
                    print(f"⏱️  [TIMING] Sandbox container execution completed in {container_elapsed:.3f}s")

                # Handle awaiting_input from shared containers
                if exec_result.get("status") == "awaiting_input":
                    execution.status = ExecutionStatus.AWAITING_INPUT
                    execution.input_prompt = exec_result.get("prompt")
                    execution.container_id = exec_result.get("container_name")
                    await db.commit()
                    return {
                        "status": "awaiting_input",
                        "execution_id": execution_id,
                        "prompt": execution.input_prompt,
                    }

                if exec_result.get("status") == "failed":
                    raise FunctionExecutionError(exec_result.get("error", "Unknown error"))

                result = exec_result.get("result")
                duration_ms = exec_result.get("duration_ms", 0)

                # Validate output
                if function.output_schema:
                    await self.validate_schema(result, function.output_schema)

                # Update execution record
                execution.status = ExecutionStatus.COMPLETED
                execution.output_data = result
                execution.completed_at = datetime.utcnow()
                execution.duration_ms = duration_ms

                await db.commit()

                # Log execution completion to Redis
                await clickhouse_logger.log_execution_end(
                    execution_id, "completed", result, None, duration_ms
                )

                return result

            except Exception as e:
                # Update execution record with error
                execution.status = ExecutionStatus.FAILED
                execution.error = str(e)
                execution.traceback = traceback.format_exc()
                execution.completed_at = datetime.utcnow()

                await db.commit()

                # Log execution failure to Redis
                await clickhouse_logger.log_execution_end(
                    execution_id, "failed", None, str(e), None
                )

                raise FunctionExecutionError(f"Function execution failed: {e}")

    async def resume_execution(
        self,
        execution_id: str,
        resume_value: Any,
    ) -> dict[str, Any]:
        """
        Resume a paused execution by writing the resume value directly
        to the container where the function is waiting on input().

        Bypasses the queue — the function thread is already running.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Execution).where(Execution.execution_id == execution_id)
            )
            execution = result.scalar_one_or_none()

            if not execution:
                raise FunctionExecutionError(f"Execution {execution_id} not found")

            if execution.status != ExecutionStatus.AWAITING_INPUT:
                raise FunctionExecutionError(
                    f"Execution {execution_id} is not awaiting input (status={execution.status})"
                )

            container_id = execution.container_id
            if not container_id:
                raise FunctionExecutionError(
                    f"Execution {execution_id} has no container_id — cannot resume"
                )

            # Write resume file into the container
            import docker

            client = docker.from_env()
            try:
                container = client.containers.get(container_id)
            except docker.errors.NotFound:
                # Container gone — mark execution as failed
                execution.status = ExecutionStatus.FAILED
                execution.error = "Container no longer available (restarted?)"
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise FunctionExecutionError(
                    f"Container {container_id} not found — execution cannot be resumed"
                )

            # Write resume data into the container via stdin pipe
            resume_payload = json.dumps({"value": resume_value}).encode("utf-8")
            resume_file = f"/tmp/exec_resume_{execution_id}.json"

            api = container.client.api
            exec_id = api.exec_create(
                container.id,
                [
                    "python3", "-c",
                    f'import sys; open("{resume_file}","wb").write(sys.stdin.buffer.read())',
                ],
                stdin=True,
                stdout=True,
                stderr=True,
            )["Id"]
            sock = api.exec_start(exec_id, socket=True)
            sock._sock.sendall(resume_payload)
            import socket as _sock_mod
            sock._sock.shutdown(_sock_mod.SHUT_WR)
            sock.read()
            sock.close()

            execution.status = ExecutionStatus.RUNNING
            await db.commit()

            # Poll for result from the container
            result_file = f"/tmp/exec_result_{execution_id}.json"
            function_timeout = settings.function_timeout

            exec_result = await asyncio.to_thread(
                container.exec_run,
                cmd=[
                    "python3",
                    "-c",
                    f"""
import sys, json, time, os
max_wait = {function_timeout}
start = time.time()
while time.time() - start < max_wait:
    try:
        with open("{result_file}", "r") as f:
            result = json.load(f)
            print(json.dumps(result))
            sys.exit(0)
    except FileNotFoundError:
        time.sleep(0.1)
        continue
print(json.dumps({{"error": "Resume timeout after {function_timeout}s"}}))
sys.exit(1)
""",
                ],
                demux=True,
            )

            stdout, stderr = exec_result.output
            stdout_str = stdout.decode() if stdout else ""

            if exec_result.exit_code != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                execution.status = ExecutionStatus.FAILED
                execution.error = error_msg
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise FunctionExecutionError(f"Resume failed: {error_msg}")

            result_data = json.loads(stdout_str)

            # Handle another awaiting_input (multiple input() calls)
            if result_data.get("status") == "awaiting_input":
                execution.status = ExecutionStatus.AWAITING_INPUT
                execution.input_prompt = result_data.get("prompt")
                await db.commit()
                return {
                    "status": "awaiting_input",
                    "execution_id": execution_id,
                    "prompt": execution.input_prompt,
                }

            if result_data.get("status") == "failed":
                execution.status = ExecutionStatus.FAILED
                execution.error = result_data.get("error", "Unknown error")
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise FunctionExecutionError(result_data.get("error", "Unknown error"))

            # Completed
            output = result_data.get("result")
            duration_ms = result_data.get("duration_ms", 0)

            execution.status = ExecutionStatus.COMPLETED
            execution.output_data = output
            execution.completed_at = datetime.utcnow()
            execution.duration_ms = duration_ms
            execution.container_id = None

            await db.commit()

            await clickhouse_logger.log_execution_end(
                execution_id, "completed", output, None, duration_ms
            )

            return output

    async def enqueue_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Enqueue a function for execution via the job queue.

        Creates an Execution record with PENDING status and dispatches to the queue.
        Returns immediately with job_id and execution_id.
        """
        from app.services.queue_service import queue_service

        # Create Execution record with PENDING status
        async with AsyncSessionLocal() as db:
            execution = Execution(
                user_id=user_id,
                execution_id=execution_id,
                function_name=function_name,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
                chat_id=chat_id,
                status=ExecutionStatus.PENDING,
                input_data=input_data,
            )
            db.add(execution)
            await db.commit()

        # Enqueue via queue service
        job_id = await queue_service.enqueue_function(
            function_namespace=function_namespace,
            function_name=function_name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            user_id=user_id,
            chat_id=chat_id,
        )

        return {
            "status": "queued",
            "execution_id": execution_id,
            "job_id": job_id,
        }

    def clear_cache(self):
        """Clear function cache."""
        self.functions_cache.clear()


# Global executor instance
executor = FunctionExecutor()
