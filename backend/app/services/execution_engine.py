"""Function execution engine with tracking and validation."""
import ast
import asyncio
import json
import logging
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

import dill
import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.execution import Execution, ExecutionStatus
from app.models.function import Function
from app.services.clickhouse_logger import clickhouse_logger
from app.services.tracking import ExecutionTracker

logger = logging.getLogger(__name__)


class FunctionExecutionError(Exception):
    pass


class SchemaValidationError(Exception):
    pass


class TrackingDecorator:
    def __init__(self, tracker: ExecutionTracker):
        self.tracker = tracker

    def __call__(self, func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            async def async_wrapper(*args, **kwargs):
                return await self.tracker.track_function_call(func, args, kwargs)

            return async_wrapper
        else:
            # For sync functions, make wrapper async so tracking works in event loop
            async def async_sync_wrapper(*args, **kwargs):
                return await self.tracker.track_function_call(func, args, kwargs)

            return async_sync_wrapper


class ASTInjector:
    @staticmethod
    def inject_tracking_decorator(code: str, tracker_name: str = "track") -> str:
        """Inject @track decorator on all function definitions using AST manipulation."""
        tree = ast.parse(code)

        class FunctionDecorator(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                # Add @track decorator if not already present
                track_decorator = ast.Name(id=tracker_name, ctx=ast.Load())

                # Check if decorator already exists
                has_track_decorator = any(
                    isinstance(d, ast.Name) and d.id == tracker_name for d in node.decorator_list
                )

                if not has_track_decorator:
                    node.decorator_list.insert(0, track_decorator)

                return self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                # Same logic for async functions
                track_decorator = ast.Name(id=tracker_name, ctx=ast.Load())

                has_track_decorator = any(
                    isinstance(d, ast.Name) and d.id == tracker_name for d in node.decorator_list
                )

                if not has_track_decorator:
                    node.decorator_list.insert(0, track_decorator)

                return self.generic_visit(node)

        transformer = FunctionDecorator()
        modified_tree = transformer.visit(tree)

        # Fix missing locations for new nodes
        ast.fix_missing_locations(modified_tree)

        # Convert back to code
        import astor

        return astor.to_source(modified_tree)


class FunctionExecutor:
    def __init__(self):
        self.functions_cache: dict[str, Function] = {}
        self.namespace_cache: dict[str, dict[str, Any]] = {}
        self._container_manager = None

    @property
    def container_manager(self):
        """Lazy load container manager (always enabled for security)."""
        if self._container_manager is None:
            from app.services.user_container_manager import container_manager

            self._container_manager = container_manager
        return self._container_manager

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
    ) -> dict[str, Any]:
        """
        Execute function in shared worker pool (separate worker containers).

        SECURITY: Only use for trusted, admin-created functions.
        Workers are separate containers from backend but shared across users.
        No per-user isolation - functions must be trusted.
        """
        # Execute in shared worker container
        exec_result = await self.worker_manager.execute_function(
            user_id=user_id,
            user_email=user_email,
            access_token=access_token,
            function_namespace=function.namespace,
            function_name=function.name,
            enabled_namespaces=function.enabled_namespaces or [],
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            chat_id=chat_id,
            db=db,
        )

        return exec_result

    async def load_function(
        self, db: AsyncSession, function_namespace: str, function_name: str, user_id: str
    ) -> Function:
        """Load function from database with caching.

        Note: No permission checks here - permissions should be validated at entry points
        (agent tool execution, webhook validation, schedule authorization).
        """
        cache_key = f"{function_namespace}:{function_name}"
        if cache_key in self.functions_cache:
            return self.functions_cache[cache_key]

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

        self.functions_cache[cache_key] = function
        return function

    async def build_execution_namespace(
        self,
        db: AsyncSession,
        execution_id: str,
        user_id: str,
        user_email: str,
        access_token: str,
        trigger_type: str,
        chat_id: Optional[str],
        function_namespace: str,
        enabled_namespaces: list[str],
    ) -> dict[str, Any]:
        """
        Build namespace with functions from enabled namespaces + own namespace.
        Functions are organized as nested namespace objects (e.g., payments.charge()).
        """
        cache_key = f"{user_id}:{execution_id}"
        if cache_key in self.namespace_cache:
            return self.namespace_cache[cache_key]

        from types import SimpleNamespace

        from app.models.user import UserRole

        # Get user's groups
        groups_result = await db.execute(
            select(UserRole.role_id).where(UserRole.user_id == user_id)
        )
        group_ids = [row[0] for row in groups_result.all()]

        # Determine which namespaces to load
        # Always include own namespace, plus enabled_namespaces
        allowed_namespaces = [function_namespace] + (enabled_namespaces or [])

        # Load functions from allowed namespaces that user has access to
        result = await db.execute(
            select(Function).where(
                Function.is_active == True,
                Function.namespace.in_(allowed_namespaces),
                (Function.user_id == user_id) | (Function.group_id.in_(group_ids)),
            )
        )
        functions = result.scalars().all()

        # Create tracker for this execution
        tracker = ExecutionTracker(execution_id, db, user_id)
        track_decorator = TrackingDecorator(tracker)

        # Build context object
        context = {
            "user_id": user_id,
            "user_email": user_email,
            "access_token": access_token,
            "execution_id": execution_id,
            "trigger_type": trigger_type,
            "chat_id": chat_id,
        }

        # Build namespace with full Python access
        # Isolation is provided by Docker containers per user
        namespace = {
            "track": track_decorator,
            "context": context,
            "__builtins__": __builtins__,
            "json": json,
            "datetime": datetime,
            "uuid": uuid,
        }

        # Group functions by namespace
        namespace_objects = {}
        for func in functions:
            try:
                # Inject tracking decorator
                modified_code = ASTInjector.inject_tracking_decorator(func.code)

                # Compile in temporary namespace
                temp_ns = {
                    "__builtins__": __builtins__,
                    "track": track_decorator,
                    "context": context,
                    "json": json,
                    "datetime": datetime,
                    "uuid": uuid,
                }
                compiled_code = compile(
                    modified_code, f"<function:{func.namespace}/{func.name}>", "exec"
                )
                exec(compiled_code, temp_ns)

                # Create namespace object if not exists
                if func.namespace not in namespace_objects:
                    namespace_objects[func.namespace] = SimpleNamespace()

                # Attach function to namespace object
                # The function is whatever was defined in the code (usually matches func.name)
                # Find the actual function object (skip builtins and our injected vars)
                for key, value in temp_ns.items():
                    if key not in [
                        "__builtins__",
                        "track",
                        "context",
                        "json",
                        "datetime",
                        "uuid",
                    ] and callable(value):
                        setattr(namespace_objects[func.namespace], func.name, value)
                        break

            except Exception as e:
                print(f"Error loading function {func.namespace}/{func.name}: {e}")
                continue

        # Add namespace objects to main namespace
        namespace.update(namespace_objects)

        self.namespace_cache[cache_key] = namespace
        return namespace

    async def execute_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        resume_data: Optional[dict[str, Any]] = None,
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

            # Check if resuming existing execution
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
            elif resume_data is not None:
                # Resuming paused execution
                if execution.status != ExecutionStatus.AWAITING_INPUT:
                    raise FunctionExecutionError(f"Execution {execution_id} is not awaiting input")

                execution.status = ExecutionStatus.RUNNING
                await db.commit()

            try:
                # Load function definition
                function = await self.load_function(db, function_namespace, function_name, user_id)

                # Validate input (only for new executions)
                if not resume_data and function.input_schema:
                    # Validate and coerce types (handles string -> number, etc.)
                    input_data = await self.validate_schema(input_data, function.input_schema)

                start_time = time.time()

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
                    )
                    elapsed = time.time() - start_time
                    print(f"⏱️  [TIMING] Shared pool execution completed in {elapsed:.3f}s")
                else:
                    # Execute in isolated Docker container (per-user, untrusted code)
                    print(
                        f"⏱️  [TIMING] Executing {function_namespace}/{function_name} in isolated container for user {user_id}"
                    )
                    print(
                        f"🐳 CONTAINER: Executing {function_namespace}/{function_name} in isolated container for user {user_id}"
                    )
                    container_start = time.time()
                    exec_result = await self.container_manager.execute_function(
                        user_id=user_id,
                        user_email=user_email,
                        access_token=access_token,
                        function_namespace=function_namespace,
                        function_name=function_name,
                        enabled_namespaces=function.enabled_namespaces or [],
                        input_data=input_data,
                        execution_id=execution_id,
                        trigger_type=trigger_type,
                        chat_id=chat_id,
                        db=db,
                    )
                    container_elapsed = time.time() - container_start
                    print(f"⏱️  [TIMING] Container execution completed in {container_elapsed:.3f}s")

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

    async def _execute_generator(
        self,
        execution: Execution,
        func: Callable,
        input_data: dict[str, Any],
        resume_data: Optional[dict[str, Any]],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Execute a generator function with pause/resume support."""

        # Resume existing generator or create new one
        if execution.generator_state and resume_data is not None:
            # Unpickle the generator
            gen = dill.loads(execution.generator_state)
            # Send user input to resume
            try:
                yielded_value = gen.send(resume_data)
            except StopIteration as e:
                # Generator completed (returned)
                result = e.value if e.value is not None else {}

                execution.status = ExecutionStatus.COMPLETED
                execution.output_data = result
                execution.completed_at = datetime.utcnow()
                execution.generator_state = None
                execution.input_prompt = None
                execution.input_schema = None

                await db.commit()

                await clickhouse_logger.log_execution_end(
                    execution.execution_id, "completed", result, None, None
                )

                return result
        else:
            # Start new generator
            gen = func(input_data)
            try:
                yielded_value = next(gen)
            except StopIteration as e:
                # Generator completed without yielding
                result = e.value if e.value is not None else {}

                execution.status = ExecutionStatus.COMPLETED
                execution.output_data = result
                execution.completed_at = datetime.utcnow()

                await db.commit()

                await clickhouse_logger.log_execution_end(
                    execution.execution_id, "completed", result, None, None
                )

                return result

        # Generator yielded (paused for user input)
        execution.status = ExecutionStatus.AWAITING_INPUT
        execution.input_prompt = yielded_value.get("prompt")
        execution.input_schema = yielded_value.get("schema")
        execution.generator_state = dill.dumps(gen)

        await db.commit()

        # Return awaiting input status
        return {
            "status": "awaiting_input",
            "execution_id": execution.execution_id,
            "prompt": execution.input_prompt,
            "schema": execution.input_schema,
        }

    def clear_cache(self):
        """Clear function and namespace caches."""
        self.functions_cache.clear()
        self.namespace_cache.clear()


# Global executor instance
executor = FunctionExecutor()
