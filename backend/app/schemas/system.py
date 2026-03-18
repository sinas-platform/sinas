"""System schemas."""

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response for system health endpoint."""

    warnings: list[dict[str, Any]]
    services: list[dict[str, Any]]
    host: dict[str, Any]


class ContainerRestartResponse(BaseModel):
    """Response from container restart."""

    status: str
    container: str
