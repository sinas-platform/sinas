"""Job schemas."""

from typing import Any, Optional

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    """Response for job status."""

    job_id: str
    status: str
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class JobResultResponse(BaseModel):
    """Response for job result."""

    job_id: str
    result: Any = None
