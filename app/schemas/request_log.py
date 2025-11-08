"""Request log schemas for API responses."""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class RequestLogResponse(BaseModel):
    """Response schema for request log entries."""
    request_id: str
    timestamp: datetime
    user_id: str
    user_email: str
    permission_used: str
    has_permission: bool
    method: str
    path: str
    query_params: str
    request_body: str
    user_agent: str
    referer: str
    ip_address: str
    status_code: int
    response_time_ms: int
    response_size_bytes: int
    resource_type: str
    resource_id: str
    group_id: str
    error_message: str
    error_type: str
    metadata: str

    class Config:
        from_attributes = True


class RequestLogQueryParams(BaseModel):
    """Query parameters for filtering request logs."""
    user_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    permission: Optional[str] = None
    path_pattern: Optional[str] = None
    status_code: Optional[int] = None
    limit: int = 100
    offset: int = 0


class RequestLogStatsResponse(BaseModel):
    """Aggregated statistics for request logs."""
    total_requests: int
    unique_users: int
    avg_response_time_ms: float
    error_rate: float
    top_paths: list
    top_permissions: list
