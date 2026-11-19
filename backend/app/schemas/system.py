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
    # SYSTEM_STATS_KUBERNETES=false): `services` is empty, no services
    # reason is set; cluster_unavailable_reason says it is turned off.
    services_enabled: bool = True
    # Kubernetes only: why the cluster part of the page (the release's pods,
    # their CPU/memory) can't be shown — the API is turned off, unreachable,
    # or the backend isn't allowed to use it. Everything else in this
    # response is still current. None when the cluster status is available.
    cluster_unavailable_reason: str | None = None
    # When the backend will try the Kubernetes API again (its circuit
    # breaker's window); None when it isn't backing off. The console polls
    # the cluster-dependent panels no faster than this.
    cluster_retry_after_seconds: int | None = None


class ContainerRestartResponse(BaseModel):
    """Response from container restart."""

    status: str
    container: str
