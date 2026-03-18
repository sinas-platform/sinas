"""System schemas."""

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response for system health endpoint."""

    warnings: list[str]
    services: list[dict[str, Any]]
    host: dict[str, Any]


class ContainerRestartResponse(BaseModel):
    """Response from container restart."""

    status: str
    container: str
