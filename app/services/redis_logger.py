import json
import redis.asyncio as redis
from datetime import datetime
from typing import Dict, Any, List, Optional
import asyncio

from app.core.config import settings


class RedisLogger:
    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self._connected = False
    
    async def connect(self):
        """Connect to Redis."""
        if self._connected:
            return
        
        try:
            self.redis_client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            
            # Test connection
            await self.redis_client.ping()
            self._connected = True
            
        except Exception as e:
            print(f"Failed to connect to Redis: {e}")
            self.redis_client = None
            self._connected = False
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis_client:
            await self.redis_client.close()
            self._connected = False
    
    async def log_execution_start(
        self, 
        execution_id: str, 
        function_name: str, 
        input_data: Dict[str, Any]
    ):
        """Log execution start event."""
        if not self._connected:
            return
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "execution_id": execution_id,
            "event": "execution_started",
            "function_name": function_name,
            "input_data": input_data
        }
        
        stream_key = f"execution:{execution_id}"
        
        try:
            await self.redis_client.xadd(stream_key, log_entry)
            # Set TTL for automatic cleanup (7 days)
            await self.redis_client.expire(stream_key, 604800)
        except Exception as e:
            print(f"Failed to log execution start: {e}")
    
    async def log_execution_end(
        self, 
        execution_id: str, 
        status: str, 
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None
    ):
        """Log execution end event."""
        if not self._connected:
            return
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "execution_id": execution_id,
            "event": "execution_completed",
            "status": status,
            "duration_ms": duration_ms or 0
        }
        
        if output_data:
            log_entry["output_data"] = json.dumps(output_data)
        
        if error:
            log_entry["error"] = error
        
        stream_key = f"execution:{execution_id}"
        
        try:
            await self.redis_client.xadd(stream_key, log_entry)
        except Exception as e:
            print(f"Failed to log execution end: {e}")
    
    async def log_function_call(
        self, 
        execution_id: str, 
        function_name: str, 
        step_id: str,
        input_data: Dict[str, Any]
    ):
        """Log function call within an execution."""
        if not self._connected:
            return
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "execution_id": execution_id,
            "event": "function_called",
            "function_name": function_name,
            "step_id": step_id,
            "input_data": json.dumps(input_data)
        }
        
        stream_key = f"execution:{execution_id}"
        
        try:
            await self.redis_client.xadd(stream_key, log_entry)
        except Exception as e:
            print(f"Failed to log function call: {e}")
    
    async def log_function_result(
        self, 
        execution_id: str, 
        function_name: str, 
        step_id: str,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None
    ):
        """Log function result within an execution."""
        if not self._connected:
            return
        
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "execution_id": execution_id,
            "event": "function_completed",
            "function_name": function_name,
            "step_id": step_id,
            "duration_ms": duration_ms or 0
        }
        
        if output_data:
            log_entry["output_data"] = json.dumps(output_data)
        
        if error:
            log_entry["error"] = error
        
        stream_key = f"execution:{execution_id}"
        
        try:
            await self.redis_client.xadd(stream_key, log_entry)
        except Exception as e:
            print(f"Failed to log function result: {e}")
    
    async def get_execution_logs(
        self, 
        execution_id: str, 
        start: str = "-", 
        end: str = "+",
        count: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get logs for a specific execution."""
        if not self._connected:
            return []
        
        stream_key = f"execution:{execution_id}"
        
        try:
            # Use XRANGE to get logs from the stream
            args = [stream_key, start, end]
            if count:
                args.extend(["COUNT", count])
            
            logs = await self.redis_client.xrange(*args)
            
            # Format logs
            formatted_logs = []
            for log_id, fields in logs:
                log_entry = {"id": log_id, **fields}
                formatted_logs.append(log_entry)
            
            return formatted_logs
            
        except Exception as e:
            print(f"Failed to get execution logs: {e}")
            return []
    
    async def stream_execution_logs(self, execution_id: str):
        """Stream real-time logs for an execution (for WebSocket connections)."""
        if not self._connected:
            return
        
        stream_key = f"execution:{execution_id}"
        
        try:
            # Start from the end of the stream
            last_id = "$"
            
            while True:
                # Block for new messages
                streams = await self.redis_client.xread(
                    {stream_key: last_id}, 
                    block=1000  # 1 second timeout
                )
                
                for stream, messages in streams:
                    for message_id, fields in messages:
                        last_id = message_id
                        yield {"id": message_id, **fields}
                
        except Exception as e:
            print(f"Failed to stream execution logs: {e}")
    
    async def cleanup_old_logs(self, max_age_seconds: int = 604800):  # 7 days
        """Clean up old execution logs."""
        if not self._connected:
            return
        
        try:
            # Get all execution streams
            pattern = "execution:*"
            keys = await self.redis_client.keys(pattern)
            
            for key in keys:
                # Check if key exists and has TTL
                ttl = await self.redis_client.ttl(key)
                if ttl == -1:  # No TTL set
                    await self.redis_client.expire(key, max_age_seconds)
                    
        except Exception as e:
            print(f"Failed to cleanup old logs: {e}")


# Global Redis logger instance
redis_logger = RedisLogger()