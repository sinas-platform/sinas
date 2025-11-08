"""Function execution engine with tracking and validation."""
import ast
import asyncio
import json
import time
import traceback
import uuid
import inspect
import dill
from datetime import datetime
from typing import Any, Dict, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jsonschema

from app.models.function import Function
from app.models.execution import Execution, StepExecution, ExecutionStatus
from app.core.database import AsyncSessionLocal
from app.services.tracking import ExecutionTracker
from app.services.redis_logger import redis_logger


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
            def sync_wrapper(*args, **kwargs):
                # For sync functions, we need to handle async tracking differently
                # when called from an async context
                try:
                    loop = asyncio.get_running_loop()
                    # We're in an event loop - we can't use asyncio.run()
                    # For now, just call the function directly without async tracking
                    # TODO: Implement proper async tracking from sync context
                    return func(*args, **kwargs)
                except RuntimeError:
                    # No event loop running, safe to use asyncio.run()
                    return asyncio.run(self.tracker.track_function_call(func, args, kwargs))
            return sync_wrapper


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
                    isinstance(d, ast.Name) and d.id == tracker_name
                    for d in node.decorator_list
                )

                if not has_track_decorator:
                    node.decorator_list.insert(0, track_decorator)

                return self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                # Same logic for async functions
                track_decorator = ast.Name(id=tracker_name, ctx=ast.Load())

                has_track_decorator = any(
                    isinstance(d, ast.Name) and d.id == tracker_name
                    for d in node.decorator_list
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
        self.functions_cache: Dict[str, Function] = {}
        self.namespace_cache: Dict[str, Dict[str, Any]] = {}

    async def validate_schema(self, data: Any, schema: Dict[str, Any]) -> None:
        """Validate data against JSON schema."""
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(f"Schema validation failed: {e.message}")

    async def load_function(self, db: AsyncSession, function_name: str, user_id: str) -> Function:
        """Load function from database with caching."""
        cache_key = f"{user_id}:{function_name}"
        if cache_key in self.functions_cache:
            return self.functions_cache[cache_key]

        result = await db.execute(
            select(Function).where(
                Function.user_id == user_id,
                Function.name == function_name,
                Function.is_active == True
            )
        )
        function = result.scalar_one_or_none()

        if not function:
            raise FunctionExecutionError(f"Function '{function_name}' not found or inactive")

        self.functions_cache[cache_key] = function
        return function

    async def build_execution_namespace(self, db: AsyncSession, execution_id: str, user_id: str) -> Dict[str, Any]:
        """Build namespace with all active functions and tracking decorator."""
        cache_key = f"{user_id}:{execution_id}"
        if cache_key in self.namespace_cache:
            return self.namespace_cache[cache_key]

        # Load all active functions for this user
        result = await db.execute(
            select(Function).where(
                Function.user_id == user_id,
                Function.is_active == True
            )
        )
        functions = result.scalars().all()

        # Create tracker for this execution
        tracker = ExecutionTracker(execution_id, db, user_id)
        track_decorator = TrackingDecorator(tracker)

        # Build namespace
        namespace = {
            "track": track_decorator,
            "__builtins__": __builtins__,
            "json": json,
            "datetime": datetime,
            "uuid": uuid,
        }

        # Compile and add all functions to namespace
        for func in functions:
            try:
                # Inject tracking decorator
                modified_code = ASTInjector.inject_tracking_decorator(func.code)

                # Compile and execute in namespace
                compiled_code = compile(modified_code, f"<function:{func.name}>", "exec")
                exec(compiled_code, namespace)

            except Exception as e:
                print(f"Error loading function {func.name}: {e}")
                continue

        self.namespace_cache[cache_key] = namespace
        return namespace

    async def execute_function(
        self,
        function_name: str,
        input_data: Dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        resume_data: Optional[Dict[str, Any]] = None,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a function with input validation and tracking."""
        async with AsyncSessionLocal() as db:
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
                    started_at=datetime.utcnow()
                )
                db.add(execution)
                await db.commit()

                # Log execution start to Redis
                await redis_logger.log_execution_start(
                    execution_id, function_name, input_data
                )
            elif resume_data is not None:
                # Resuming paused execution
                if execution.status != ExecutionStatus.AWAITING_INPUT:
                    raise FunctionExecutionError(f"Execution {execution_id} is not awaiting input")

                execution.status = ExecutionStatus.RUNNING
                await db.commit()

            try:
                # Load function definition
                function = await self.load_function(db, function_name, user_id)

                # Validate input (only for new executions)
                if not resume_data and function.input_schema:
                    await self.validate_schema(input_data, function.input_schema)

                # Build execution namespace
                namespace = await self.build_execution_namespace(db, execution_id, user_id)

                # Execute function
                start_time = time.time()

                if function_name in namespace:
                    func = namespace[function_name]

                    # Check if function is a generator (stateful)
                    if inspect.isgeneratorfunction(func):
                        return await self._execute_generator(
                            execution, func, input_data, resume_data, db
                        )
                    elif asyncio.iscoroutinefunction(func):
                        result = await func(input_data)
                    else:
                        result = func(input_data)
                else:
                    raise FunctionExecutionError(f"Function '{function_name}' not found in namespace")

                end_time = time.time()
                duration_ms = int((end_time - start_time) * 1000)

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
                await redis_logger.log_execution_end(
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
                await redis_logger.log_execution_end(
                    execution_id, "failed", None, str(e), None
                )

                raise FunctionExecutionError(f"Function execution failed: {e}")

    async def _execute_generator(
        self,
        execution: Execution,
        func: Callable,
        input_data: Dict[str, Any],
        resume_data: Optional[Dict[str, Any]],
        db: AsyncSession
    ) -> Dict[str, Any]:
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

                await redis_logger.log_execution_end(
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

                await redis_logger.log_execution_end(
                    execution.execution_id, "completed", result, None, None
                )

                return result

        # Generator yielded (paused for user input)
        execution.status = ExecutionStatus.AWAITING_INPUT
        execution.input_prompt = yielded_value.get('prompt')
        execution.input_schema = yielded_value.get('schema')
        execution.generator_state = dill.dumps(gen)

        await db.commit()

        # Return awaiting input status
        return {
            "status": "awaiting_input",
            "execution_id": execution.execution_id,
            "prompt": execution.input_prompt,
            "schema": execution.input_schema
        }

    def clear_cache(self):
        """Clear function and namespace caches."""
        self.functions_cache.clear()
        self.namespace_cache.clear()


# Global executor instance
executor = FunctionExecutor()
