"""System schemas."""

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response for system health endpoint."""

    warnings: list[dict[str, Any]]
    services: list[dict[str, Any]]
    host: dict[str, Any]
    # Redis memory: used_bytes, maxmemory_bytes (None = no limit), percent,
    # policy, evicted_keys, oom_errors (refused writes; None before 6.2).
    # None when Redis could not be read.
    redis: dict[str, Any] | None = None
    # "docker" | "kubernetes" — decides where services/stats come from.
    deployment: str = "docker"
    executors: dict[str, str] = {}
    # Set when the service list (or, with include_stats, CPU/memory) could
    # not be read in this deployment; the console shows it instead of data.
    services_unavailable_reason: str | None = None
    metrics_unavailable_reason: str | None = None
    # False when the service list is turned off on purpose (Kubernetes with
    # SYSTEM_STATS_KUBERNETES=false): `services` is empty, no reason is set,
    # and the console hides the panel instead of reporting an error.
    services_enabled: bool = True


class ContainerRestartResponse(BaseModel):
    """Response from container restart."""

    status: str
    container: str
