import ast
import asyncio
import inspect
import json
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import ValidationError, BaseModel
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
    
    async def load_function(self, db: AsyncSession, function_name: str, subtenant_id: str) -> Function:
        """Load function from database with caching."""
        if function_name in self.functions_cache:
            return self.functions_cache[function_name]
        
        result = await db.execute(
            select(Function).where(
                Function.subtenant_id == subtenant_id,
                Function.name == function_name,
                Function.is_active == True
            )
        )
        function = result.scalar_one_or_none()
        
        if not function:
            raise FunctionExecutionError(f"Function '{function_name}' not found or inactive")
        
        self.functions_cache[function_name] = function
        return function
    
    async def build_execution_namespace(self, db: AsyncSession, execution_id: str, subtenant_id: str) -> Dict[str, Any]:
        """Build namespace with all active functions and tracking decorator."""
        if execution_id in self.namespace_cache:
            return self.namespace_cache[execution_id]
        
        # Load all active functions for this subtenant
        result = await db.execute(
            select(Function).where(
                Function.subtenant_id == subtenant_id,
                Function.is_active == True
            )
        )
        functions = result.scalars().all()
        
        # Create tracker for this execution
        tracker = ExecutionTracker(execution_id, db, subtenant_id)
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
        
        self.namespace_cache[execution_id] = namespace
        return namespace
    
    async def execute_function(
        self,
        function_name: str,
        input_data: Dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        subtenant_id: str
    ) -> Dict[str, Any]:
        """Execute a function with input validation and tracking."""
        async with AsyncSessionLocal() as db:
            # Create execution record
            execution = Execution(
                subtenant_id=subtenant_id,
                execution_id=execution_id,
                function_name=function_name,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
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
            
            try:
                # Load function definition
                function = await self.load_function(db, function_name, subtenant_id)
                
                # Validate input
                await self.validate_schema(input_data, function.input_schema)
                
                # Build execution namespace
                namespace = await self.build_execution_namespace(db, execution_id, subtenant_id)
                
                # Execute function
                start_time = time.time()
                
                if function_name in namespace:
                    func = namespace[function_name]
                    if asyncio.iscoroutinefunction(func):
                        result = await func(input_data)
                    else:
                        result = func(input_data)
                else:
                    raise FunctionExecutionError(f"Function '{function_name}' not found in namespace")
                
                end_time = time.time()
                duration_ms = int((end_time - start_time) * 1000)
                
                # Validate output
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
    
    def clear_cache(self):
        """Clear function and namespace caches."""
        self.functions_cache.clear()
        self.namespace_cache.clear()


# Global executor instance
executor = FunctionExecutor()