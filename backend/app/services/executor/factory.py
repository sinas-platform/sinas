"""Executor factory.

Returns the configured `SandboxExecutor` / `TrustedExecutor` impl based
on `settings.sandbox_executor` / `settings.trusted_executor`.

A small cache keeps a single instance of each impl per process. The
existing legacy classes (`container_pool`, `shared_worker_manager`) are
themselves module-level singletons; the adapter wrappers are stateless,
so caching is just to avoid the trivial allocation.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.executor.base import SandboxExecutor, TrustedExecutor


@lru_cache(maxsize=1)
def get_sandbox_executor() -> SandboxExecutor | None:
    """Return the configured sandbox executor, or None when disabled.

    When `None` is returned, callers must reject any request that would
    route to the sandbox (untrusted Function + agent codeExecution).
    """
    mode = settings.sandbox_executor

    if mode == "docker_pool":
        from app.services.executor.docker_pool_sandbox import (
            DockerPoolSandboxExecutor,
        )

        return DockerPoolSandboxExecutor()

    if mode == "docker_ephemeral":
        from app.services.executor.docker_ephemeral_sandbox import (
            DockerEphemeralSandboxExecutor,
        )

        return DockerEphemeralSandboxExecutor()

    if mode == "k8s_pod":
        from app.services.executor.k8s_pod_sandbox import K8sPodSandboxExecutor

        return K8sPodSandboxExecutor()

    if mode == "disabled":
        return None

    raise ValueError(f"Unknown sandbox_executor mode: {mode!r}")


@lru_cache(maxsize=1)
def get_trusted_executor() -> TrustedExecutor | None:
    """Return the configured trusted executor, or None when disabled.

    When `None` is returned, callers must reject `Function.shared_pool`
    executions with a clear error — no silent fallback to the sandbox
    (sandbox pods are network-isolated, so trusted functions would break
    there in confusing ways rather than obviously).
    """
    mode = settings.trusted_executor

    if mode == "docker_shared":
        from app.services.executor.docker_shared_trusted import (
            DockerSharedTrustedExecutor,
        )

        return DockerSharedTrustedExecutor()

    if mode == "k8s_shared":
        from app.services.executor.k8s_shared_trusted import K8sSharedTrustedExecutor

        return K8sSharedTrustedExecutor()

    if mode == "inprocess":
        from app.services.executor.inprocess_trusted import InProcessTrustedExecutor

        return InProcessTrustedExecutor()

    if mode == "disabled":
        return None

    raise ValueError(f"Unknown trusted_executor mode: {mode!r}")


def _reset_cache_for_tests() -> None:
    """Clear the factory's LRU caches. Used by tests that override settings."""
    get_sandbox_executor.cache_clear()
    get_trusted_executor.cache_clear()
