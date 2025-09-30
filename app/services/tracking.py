import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import StepExecution, ExecutionStatus
from app.core.database import AsyncSessionLocal
from app.services.redis_logger import redis_logger


class ExecutionTracker:
    def __init__(self, execution_id: str, db: AsyncSession, subtenant_id: str):
        self.execution_id = execution_id
        self.db = db
        self.subtenant_id = subtenant_id
        self.call_stack: List[str] = []
    
    async def track_function_call(
        self, 
        func: Callable, 
        args: Tuple[Any, ...], 
        kwargs: Dict[str, Any]
    ) -> Any:
        """Track a function call with start/end times and results."""
        function_name = func.__name__
        step_id = str(uuid.uuid4())
        
        # Prepare input data (first argument is typically the input dict)
        input_data = args[0] if args else kwargs
        
        # Create step execution record
        step = StepExecution(
            subtenant_id=self.subtenant_id,
            execution_id=self.execution_id,
            function_name=function_name,
            status=ExecutionStatus.RUNNING,
            input_data=input_data,
            started_at=datetime.utcnow()
        )
        
        self.db.add(step)
        await self.db.commit()
        
        # Log function call to Redis
        await redis_logger.log_function_call(
            self.execution_id, function_name, str(step.id), input_data
        )
        
        start_time = time.time()
        
        try:
            # Add to call stack
            self.call_stack.append(function_name)
            
            # Execute function
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)
            
            # Update step with success
            step.status = ExecutionStatus.COMPLETED
            step.output_data = result
            step.completed_at = datetime.utcnow()
            step.duration_ms = duration_ms
            
            await self.db.commit()
            
            # Log function result to Redis
            await redis_logger.log_function_result(
                self.execution_id, function_name, str(step.id), 
                result, None, duration_ms
            )
            
            return result
            
        except Exception as e:
            end_time = time.time()
            duration_ms = int((end_time - start_time) * 1000)
            
            # Update step with error
            step.status = ExecutionStatus.FAILED
            step.error = str(e)
            step.completed_at = datetime.utcnow()
            step.duration_ms = duration_ms
            
            await self.db.commit()
            
            # Log function error to Redis
            await redis_logger.log_function_result(
                self.execution_id, function_name, str(step.id), 
                None, str(e), duration_ms
            )
            
            raise e
            
        finally:
            # Remove from call stack
            if self.call_stack and self.call_stack[-1] == function_name:
                self.call_stack.pop()
    
    def get_call_stack(self) -> List[str]:
        """Get current call stack."""
        return self.call_stack.copy()