"""Executor Protocols and the typed result they return.

Both `SandboxExecutor` and `TrustedExecutor` expose the same
`execute` / `resume` shape — the call signature was historically identical
between `container_pool` and `shared_worker_manager`, and we preserve that
here. The two are kept as separate Protocols so that mypy / Pyright catch
accidental misuse: a function that requires sandbox isolation cannot be
silently routed to a trusted executor (and vice versa), even though both
happen to satisfy the same call shape today.

`ExecutionResult` is the typed in-process contract between an executor impl
and `execution_engine`. The *wire* format spoken by the in-container executor
is still a JSON dict; impls translate it via `ExecutionResult.from_wire`, so
the executor image is unchanged by this refactor.

The `handle` carried on an AWAITING_INPUT result is **opaque** to the engine:
it is whatever the producing executor needs to resume (a Docker container
name today; a `namespace/pod` for the future k8s impl; a durable execution
key for a replay-based impl — see issue #79). The engine persists it and
hands it back to `resume()` unchanged.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


class ExecutorUnavailable(Exception):
    """The executor could not start the function: no worker to run it on, or
    the container/pod for it failed to come up.

    Raised only where the function's code has certainly not run yet, which
    is what makes the failure safe to retry: the function queue retries it
    with backoff (`QUEUE_MAX_RETRIES`) instead of failing the job. Anything
    that may have happened after the code started is an ordinary failure.
    """


def is_transient_infra_error(exc: BaseException) -> bool:
    """A lost connection to the database, Redis or another service: worth
    retrying, provided the function's code has not run yet."""
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    from app.core.redis import is_redis_oom

    # A full Redis (maxmemory, noeviction) refusing a write before the code
    # ran, e.g. the shared-pool admission gate: retried like an outage.
    if is_redis_oom(exc):
        return True
    if isinstance(
        exc,
        (OSError, RedisConnectionError, RedisTimeoutError, OperationalError, InterfaceError),
    ):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


@contextlib.asynccontextmanager
async def before_run(what: str) -> AsyncIterator[None]:
    """Wraps an executor's preparation, before the code is handed over
    (loading the function or its secrets, resolving the image): a lost
    connection there means the code never ran, so it surfaces as
    `ExecutorUnavailable` and the queue may retry it."""
    try:
        yield
    except ExecutorUnavailable:
        raise
    except Exception as e:
        if is_transient_infra_error(e):
            raise ExecutorUnavailable(f"{what}: {e}") from e
        raise


class ResultStatus(str, Enum):
    """Outcome of an execute()/resume() call.

    Distinct from `app.models.execution.ExecutionStatus` (the persisted
    lifecycle state) — this is only the executor's view of one call's outcome.
    """

    COMPLETED = "completed"
    AWAITING_INPUT = "awaiting_input"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionResult:
    """Typed result of one execute()/resume() call."""

    status: ResultStatus
    # COMPLETED
    result: Any = None
    duration_ms: int = 0
    # AWAITING_INPUT
    prompt: str | None = None
    handle: str | None = None  # opaque resume token, owned by the producing executor
    # FAILED
    error: str | None = None
    traceback: str | None = None

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> "ExecutionResult":
        """Translate the in-container executor's JSON dict into a typed result.

        Mirrors how `execution_engine` historically interpreted the dict:
        an explicit `awaiting_input`/`failed` status, otherwise success.
        Keeps the wire format (and the executor image) unchanged.
        """
        status = d.get("status")
        if status == "awaiting_input":
            return cls(
                ResultStatus.AWAITING_INPUT,
                prompt=d.get("prompt"),
                handle=d.get("container_name"),
            )
        # Explicit failure, or a status-less error envelope (e.g. the poll
        # script's own "Execution timeout" output) — never treat as success.
        if status == "failed" or (status != "completed" and d.get("error")):
            return cls(
                ResultStatus.FAILED,
                error=d.get("error", "Unknown error"),
                traceback=d.get("traceback"),
            )
        return cls(
            ResultStatus.COMPLETED,
            result=d.get("result"),
            duration_ms=d.get("duration_ms", 0),
        )

    @classmethod
    def failed(cls, error: str, traceback: str | None = None) -> "ExecutionResult":
        return cls(ResultStatus.FAILED, error=error, traceback=traceback)


@runtime_checkable
class SandboxExecutor(Protocol):
    """Runs untrusted function code. Per-execution isolation required.

    Implementations must ensure that state written by one execution cannot
    leak to a subsequent execution (file system, env vars, network
    artifacts). Concrete impls today rely on Docker-level isolation; the
    k8s impl will rely on Pod-level isolation; future impls may rely on
    OS-level or VM-level sandboxing. The Protocol does not encode the
    mechanism, only the contract.
    """

    async def execute(
        self,
        *,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: str | None,
        user_custom_fields: dict[str, Any] | None,
        db: AsyncSession,
        timeout: int,
    ) -> ExecutionResult: ...

    async def resume(
        self,
        *,
        handle: str,
        resume_value: Any,
        execution_id: str,
        timeout: int,
    ) -> ExecutionResult:
        """Resume an execution paused on input().

        `handle` is the opaque token from a prior AWAITING_INPUT result. The
        call is DB-free: the engine owns all `Execution` persistence and maps
        the returned `ExecutionResult` to lifecycle state.

        NOTE: a future durable/replay executor (issue #79) will additionally
        need the execution *context* (original input_data + accumulated resume
        history) to replay deterministically; that param can be added to this
        Protocol when that impl lands. Today's live executors only need the
        handle.
        """
        ...


@runtime_checkable
class TrustedExecutor(Protocol):
    """Runs admin-approved code (`Function.shared_pool=True`).

    No per-execution isolation requirement. Implementations may run code
    in-process, in a shared long-lived container, or any other shape that
    optimizes for throughput. Callers must only route code here when an
    admin has explicitly opted the function into the trusted pool.
    """

    async def execute(
        self,
        *,
        user_id: str,
        user_email: str,
        access_token: str,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        chat_id: str | None,
        user_custom_fields: dict[str, Any] | None,
        db: AsyncSession,
        timeout: int,
    ) -> ExecutionResult: ...

    async def resume(
        self,
        *,
        handle: str,
        resume_value: Any,
        execution_id: str,
        timeout: int,
    ) -> ExecutionResult: ...
