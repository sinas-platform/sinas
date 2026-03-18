"""Worker schemas."""

from pydantic import BaseModel


class WorkerResponse(BaseModel):
    """Worker information response."""

    id: str
    container_name: str
    status: str
    created_at: str
    executions: int


class ScaleWorkersRequest(BaseModel):
    """Request to scale workers."""

    target_count: int


class ScaleWorkersResponse(BaseModel):
    """Response from scaling workers."""

    action: str
    previous_count: int
    current_count: int
    added: int = 0
    removed: int = 0


class WorkerCountResponse(BaseModel):
    """Response for worker count."""

    count: int


class WorkerReloadResponse(BaseModel):
    """Response from reloading worker packages."""

    workers: dict
    pool: dict
