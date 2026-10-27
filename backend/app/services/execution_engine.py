"""Function execution engine with tracking and validation."""
import asyncio
import contextlib
import json
import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.execution import Execution, ExecutionStatus
from app.models.function import Function
from app.services.clickhouse_logger import clickhouse_logger
from app.services.executor.base import (
    ExecutionResult,
    ExecutorUnavailable,
    ResultStatus,
    is_transient_infra_error,
)
from app.services.shared_admission import SharedPoolSaturated

logger = logging.getLogger(__name__)


class FunctionExecutionError(Exception):
    """A function execution failed for good (the Execution row is FAILED).

    The two flags tell the queue worker what kind of failure it was:
    `transient` — the infrastructure failed before the function's code ran;
    `dead_letter` — a failure the queue was allowed to retry, which ran out
    of attempts, so the job belongs in the dead-letter queue.
    """

    transient = False
    dead_letter = False
    # The engine recorded the failure itself (row FAILED, callback, batch).
    recorded = False


class ExecutionAlreadyCancelled(FunctionExecutionError):
    """A re-delivered job of an execution that was cancelled: not run; the
    queue reports it as cancelled, not failed."""

    recorded = True


# The two kinds of failed attempt, each with its own retry budget.
INFRASTRUCTURE = "infrastructure"  # the code never ran: QUEUE_MAX_RETRIES
FUNCTION = "function"  # the function's own failure: Function.max_retries


def dead_letter_note(attempts: int) -> str:
    """Appended to a dead-lettered execution's error."""
    return (
        f" (failed {attempts} attempt{'s' if attempts != 1 else ''}; "
        "moved to the dead-letter queue)"
    )


class ExecutionRetryScheduled(Exception):
    """A queued execution failed but has attempts left (issue #135).

    The engine put the Execution row back to PENDING with the error noted,
    and skipped the failure bookkeeping (callback, batch, execution log):
    nothing has terminated yet. The queue worker re-runs the job after a
    backoff. `kind` is INFRASTRUCTURE or FUNCTION; `attempt` counts that
    kind's failures only.
    """

    def __init__(self, message: str, *, kind: str, attempt: int, max_attempts: int):
        super().__init__(message)
        self.kind = kind
        self.attempt = attempt
        self.max_attempts = max_attempts


class SchemaValidationError(Exception):
    pass


async def _save_retry_note(execution_id: str, note: str, error_traceback: str) -> None:
    """Mark an execution PENDING with its retry note through a fresh session
    (the caller's failed). Best effort: the retry goes ahead regardless."""
    from sqlalchemy import update

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Execution)
                .where(
                    Execution.execution_id == execution_id,
                    Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
                )
                .values(status=ExecutionStatus.PENDING, error=note, traceback=error_traceback)
            )
            await db.commit()
    except Exception:
        logger.warning(f"Could not note the retry of execution {execution_id}", exc_info=True)


async def _fire_callback(
    *,
    db: AsyncSession,
    execution: "Execution",
    callback_url: str,
    user_id: str,
    user_email: str,
    status: str,  # "success" | "failure"
    result: Any,
    error: str | None,
    trigger_id: str,
    function_namespace: str,
    function_name: str,
) -> None:
    """Fire-and-forget POST to the caller-supplied callback URL.

    Authenticated with a freshly-minted user access token so the receiving
    app can validate via the normal Sinas auth path. No retries — apps
    fall back to polling `/executions/{id}/status` if they need to
    reconcile.

    Records `callback_status` and `callback_response_code` on the
    Execution row for operator visibility.
    """
    from app.core.auth import create_access_token

    # Short callback-only token (just long enough to be received and validated).
    cb_token = create_access_token(user_id, user_email, expires_delta=timedelta(minutes=5))

    payload = {
        "execution_id": execution.execution_id,
        "trigger_id": trigger_id,
        "function": {"namespace": function_namespace, "name": function_name},
        "status": status,
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "finished_at": execution.completed_at.isoformat() if execution.completed_at else None,
        "duration_ms": execution.duration_ms,
        "result": result,
        "error": error,
    }

    status_label = "failed"
    response_code: int | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                callback_url,
                headers={
                    "Authorization": f"Bearer {cb_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload, default=str),
            )
        response_code = resp.status_code
        status_label = "sent" if 200 <= resp.status_code < 300 else "failed"
    except httpx.HTTPError as e:
        logger.warning(
            "Callback POST failed for execution %s → %s: %s",
            execution.execution_id, callback_url, e,
        )
    except Exception as e:
        logger.warning(
            "Unexpected callback error for execution %s → %s: %s",
            execution.execution_id, callback_url, e,
        )

    try:
        execution.callback_status = status_label
        execution.callback_response_code = response_code
        await db.commit()
    except Exception:
        # Don't let callback bookkeeping mask the underlying execution result.
        logger.exception("Failed to persist callback status for %s", execution.execution_id)


class FunctionExecutor:
    def __init__(self):
        self.functions_cache: dict[str, Function] = {}
        # Executor selection is delegated to `app.services.executor.factory`,
        # which resolves the configured impls from settings. We cache the
        # resolved handles per-instance to avoid the factory call per
        # execution. Either handle is `None` when that execution class is
        # disabled in this deployment (sandbox_executor / trusted_executor
        # = "disabled").
        self._sandbox_executor = None
        self._sandbox_executor_resolved = False
        self._trusted_executor = None
        self._trusted_executor_resolved = False

    @property
    def sandbox_executor(self):
        """Resolved SandboxExecutor for untrusted code (or None if disabled).

        Untrusted Function execution + agent `codeExecution` route here. A
        `None` value means the deploy explicitly disabled the sandbox; callers
        must surface a clear error rather than silently fall back to trusted.
        """
        if not self._sandbox_executor_resolved:
            from app.services.executor import get_sandbox_executor

            self._sandbox_executor = get_sandbox_executor()
            self._sandbox_executor_resolved = True
        return self._sandbox_executor

    @property
    def trusted_executor(self):
        """Resolved TrustedExecutor for admin-approved `Function.shared_pool`
        code (or None if disabled — callers must surface a clear error)."""
        if not self._trusted_executor_resolved:
            from app.services.executor import get_trusted_executor

            self._trusted_executor = get_trusted_executor()
            self._trusted_executor_resolved = True
        return self._trusted_executor

    async def validate_schema(self, data: Any, schema: dict[str, Any]) -> Any:
        """
        Validate data against JSON schema with type coercion.

        Returns:
            Coerced data
        """
        from app.utils.schema import validate_with_coercion

        try:
            return validate_with_coercion(data, schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(f"Schema validation failed: {e.message}")

    async def _execute_in_shared_pool(
        self,
        function: Function,
        input_data: dict[str, Any],
        execution_id: str,
        user_id: str,
        user_email: str,
        access_token: str,
        trigger_type: str,
        chat_id: str | None,
        db: AsyncSession,
        timeout: int | None = None,
        user_custom_fields: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """
        Execute function in shared worker pool (separate worker containers).

        SECURITY: Only use for trusted, admin-created functions.
        Workers are separate containers from backend but shared across users.
        No per-user isolation - functions must be trusted.
        """
        if self.trusted_executor is None:
            raise FunctionExecutionError(
                f"Trusted (shared-pool) execution is disabled on this deployment "
                f"(trusted_executor='disabled'); cannot run "
                f"{function.namespace}/{function.name}. Either enable a trusted "
                f"executor or clear the function's shared_pool flag."
            )
        exec_result = await self.trusted_executor.execute(
            user_id=user_id,
            user_email=user_email,
            user_custom_fields=user_custom_fields,
            access_token=access_token,
            function_namespace=function.namespace,
            function_name=function.name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            chat_id=chat_id,
            db=db,
            timeout=timeout,
        )

        return exec_result

    async def load_function(
        self, db: AsyncSession, function_namespace: str, function_name: str, user_id: str
    ) -> Function:
        """Load function from database with caching.

        Cache is invalidated when the function's updated_at changes,
        so code/config edits are picked up without process restart.

        Note: No permission checks here - permissions should be validated at entry points
        (agent tool execution, webhook validation, schedule authorization).
        """
        cache_key = f"{function_namespace}:{function_name}"

        result = await db.execute(
            select(Function).where(
                Function.namespace == function_namespace,
                Function.name == function_name,
                Function.is_active == True,
            )
        )
        function = result.scalar_one_or_none()

        if not function:
            raise FunctionExecutionError(
                f"Function '{function_namespace}/{function_name}' not found or inactive"
            )

        # Invalidate cache if function was updated since last load
        cached = self.functions_cache.get(cache_key)
        if cached and cached.updated_at != function.updated_at:
            logger.info(f"Function cache invalidated: {cache_key} (updated)")

        self.functions_cache[cache_key] = function
        return function

    @staticmethod
    def _already_finished(execution: Optional[Execution]) -> Optional[tuple[Any]]:
        """None when the execution may run (no row yet, or PENDING, RUNNING
        or AWAITING_INPUT). For one that already reached a final state:
        COMPLETED → a 1-tuple with its stored output, the run's result;
        FAILED → a FunctionExecutionError marked recorded (its bookkeeping
        was done when it ended), dead-lettered again only if it was then;
        CANCELLED → ExecutionAlreadyCancelled."""
        if execution is None or execution.status not in (
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
        ):
            return None
        execution_id = execution.execution_id
        logger.warning(
            f"Execution {execution_id} is already {execution.status.value}; not running it again"
        )
        if execution.status is ExecutionStatus.COMPLETED:
            return (execution.output_data,)
        if execution.status is ExecutionStatus.CANCELLED:
            raise ExecutionAlreadyCancelled(f"Execution {execution_id} was cancelled")
        err = FunctionExecutionError(execution.error or f"Execution {execution_id} already failed")
        err.recorded = True
        # Its job went to the DLQ then (the note says so) but the entry may
        # not have been written (Redis full): write it now.
        err.dead_letter = "moved to the dead-letter queue" in (execution.error or "")
        raise err

    async def execute_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: str | None = None,
        callback_url: str | None = None,
        depth: int = 0,
        prior_failures: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Execute a function with input validation and tracking.

        `depth` is this execution's nesting depth (0 = top-level). It is
        embedded in the per-execution access token so a nested call can
        compute its own depth and be bounded by settings.max_execution_depth.

        `prior_failures` counts the earlier failed runs of this queue job per
        kind, {INFRASTRUCTURE: n, FUNCTION: m} (None = not run by the queue:
        never retried). Each kind has its own budget, so an outage never uses
        up a function's retries (nor the reverse):

        - the infrastructure failed before the code ran (`ExecutorUnavailable`,
          or a lost DB/Redis connection before dispatch): up to
          settings.queue_max_retries times;
        - the function's own code failed: up to `Function.max_retries` times
          (default 0 — functions may have side effects, retrying is opt-in);
        - anything deterministic (input/output schema validation, a disabled
          executor): never.

        A failure with attempts left raises `ExecutionRetryScheduled` and
        leaves the row PENDING; otherwise the row is FAILED and the raised
        `FunctionExecutionError` says whether to dead-letter the job.
        """
        # Single choke point for every trigger type (API, webhook, schedule,
        # agent tool, CDC), so one guard disables user-code execution platform
        # wide rather than each entry point remembering to check.
        if not settings.code_execution_enabled:
            raise FunctionExecutionError(
                "Function execution is disabled on this deployment "
                "(CODE_EXECUTION_ENABLED=false)."
            )
        # Metering leaf: one op per function execution, whatever triggered it
        from app.services import metering

        await metering.record(metering.OperationKind.FUNCTION)
        async with AsyncSessionLocal() as db:
            # Nothing has run yet: an unreachable database (or Redis) here
            # is an infrastructure failure the queue may retry.
            try:
                # Get user info for context
                from app.core.auth import create_access_token
                from app.models.user import User

                user_result = await db.execute(select(User).where(User.id == user_id))
                user = user_result.scalar_one_or_none()
                user_email = user.email if user else "unknown@unknown.com"
                user_custom_fields = (user.custom_fields or {}) if user else {}

                # Load function early so we can size the access-token TTL to its
                # configured timeout. (load_function is cached, so this is cheap.)
                function = await self.load_function(db, function_namespace, function_name, user_id)
                function_timeout = function.timeout or settings.function_timeout

                # Per-execution access token. TTL = function timeout + buffer, so
                # the token outlives the run (incl. downstream calls into apps that
                # forward it back to Sinas) but not by much. Capped at the global
                # ceiling. See ADR 2026-05-14-external-app-bulk-enqueue.md §2.2.
                token_ttl = timedelta(
                    seconds=min(
                        function_timeout + settings.execution_token_buffer_seconds,
                        settings.max_execution_token_seconds,
                    )
                )
                # It carries the caller's attribution context: chats the function
                # starts through Sinas are attributed as the caller's would be.
                from app.core.auth import current_credential_context

                access_token = create_access_token(
                    user_id, user_email, expires_delta=token_ttl, execution_depth=depth,
                    context=current_credential_context(),
                )

                # Check if execution already exists (e.g. created by enqueue_function)
                result = await db.execute(
                    select(Execution).where(Execution.execution_id == execution_id)
                )
                execution = result.scalar_one_or_none()

                # A finished execution never runs again, whatever re-delivers
                # its job: a function may have side effects. (A queue job is
                # re-run when its finish could not be recorded, e.g. Redis
                # full at that moment.) Inside this try, so a database blip
                # here is retried like any other before the run.
                finished = self._already_finished(execution)
                if finished is not None:
                    return finished[0]

                if not execution:
                    # Create new execution record
                    execution = Execution(
                        user_id=user_id,
                        execution_id=execution_id,
                        function_name=function_name,
                        trigger_type=trigger_type,
                        trigger_id=trigger_id,
                        chat_id=chat_id,
                        status=ExecutionStatus.RUNNING,
                        input_data=input_data,
                        started_at=datetime.utcnow(),
                    )
                    db.add(execution)
                    await db.commit()

                    # Log execution start to Redis
                    await clickhouse_logger.log_execution_start(execution_id, function_name, input_data)
            except FunctionExecutionError:
                raise
            except Exception as e:
                if is_transient_infra_error(e):
                    raise ExecutorUnavailable(f"Could not prepare the execution: {e}") from e
                raise

            # Set once the code is handed to an executor: a failure before
            # that is never the function's own.
            dispatched = False
            # Set once the code returned a valid result: from then on nothing
            # is retried (it would run the function a second time).
            succeeded = False
            try:
                # `function` and `function_timeout` were loaded above (they
                # parameterized the access-token TTL).

                # Validate input
                if function.input_schema:
                    # Validate and coerce types (handles string -> number, etc.)
                    input_data = await self.validate_schema(input_data, function.input_schema)

                start_time = time.time()

                # Executor role — drives where an awaiting-input execution is
                # resumed (persisted as a "role:handle" prefix in container_id).
                role = "trusted" if function.shared_pool else "sandbox"

                # Route execution based on shared_pool setting
                if function.shared_pool:
                    # Execute in shared worker container pool
                    print(
                        f"⏱️  [TIMING] Executing {function_namespace}/{function_name} in shared pool"
                    )
                    # Depth-aware admission: reserve worker slots for nested
                    # calls so a parent waiting on a child can't starve the pool
                    # (the nested-execution deadlock). Opt-in via
                    # settings.shared_pool_reserve; nested calls (depth > 0)
                    # bypass the gate.
                    from app.services.shared_admission import shared_pool_admission

                    # SharedPoolSaturated propagates raw from here — the
                    # outer handler resets the row to PENDING and re-raises so
                    # the queue worker can defer-and-retry instead of failing.
                    async with shared_pool_admission(depth, execution_id):
                        dispatched = self.trusted_executor is not None
                        exec_result = await self._execute_in_shared_pool(
                            function=function,
                            input_data=input_data,
                            execution_id=execution_id,
                            user_id=user_id,
                            user_email=user_email,
                            user_custom_fields=user_custom_fields,
                            access_token=access_token,
                            trigger_type=trigger_type,
                            chat_id=chat_id,
                            db=db,
                            timeout=function_timeout,
                        )
                    elapsed = time.time() - start_time
                    print(f"⏱️  [TIMING] Shared pool execution completed in {elapsed:.3f}s")
                else:
                    # Execute in pooled Docker container (untrusted code)
                    print(
                        f"⏱️  [TIMING] Executing {function_namespace}/{function_name} in sandbox container"
                    )
                    sandbox = self.sandbox_executor
                    if sandbox is None:
                        raise FunctionExecutionError(
                            f"Sandbox execution is disabled on this deployment "
                            f"(sandbox_executor='disabled'); cannot run "
                            f"untrusted function '{function_namespace}/{function_name}'. "
                            f"Either set Function.shared_pool=True for admin-approved "
                            f"functions, or enable a sandbox executor."
                        )
                    container_start = time.time()
                    dispatched = True
                    exec_result = await sandbox.execute(
                        user_id=user_id,
                        user_email=user_email,
                        user_custom_fields=user_custom_fields,
                        access_token=access_token,
                        function_namespace=function_namespace,
                        function_name=function_name,
                        input_data=input_data,
                        execution_id=execution_id,
                        trigger_type=trigger_type,
                        chat_id=chat_id,
                        db=db,
                        timeout=function_timeout,
                    )
                    container_elapsed = time.time() - container_start
                    print(f"⏱️  [TIMING] Sandbox container execution completed in {container_elapsed:.3f}s")

                # Handle awaiting_input (only the trusted/shared path can pause;
                # sandbox mode raises on input()).
                if exec_result.status is ResultStatus.AWAITING_INPUT:
                    execution.status = ExecutionStatus.AWAITING_INPUT
                    execution.input_prompt = exec_result.prompt
                    # "role:handle" — role picks the executor at resume time;
                    # handle is the executor's opaque resume token.
                    execution.container_id = f"{role}:{exec_result.handle}"
                    await db.commit()
                    return {
                        "status": "awaiting_input",
                        "execution_id": execution_id,
                        "prompt": execution.input_prompt,
                    }

                if exec_result.status is ResultStatus.FAILED:
                    err = FunctionExecutionError(exec_result.error or "Unknown error")
                    # The container captured the function's real internal traceback.
                    # Attach it so the except handler persists it instead of the
                    # backend's own (useless) stack pointing at this raise.
                    err.container_traceback = exec_result.traceback
                    raise err

                result = exec_result.result
                duration_ms = exec_result.duration_ms

                # Validate output
                if function.output_schema:
                    await self.validate_schema(result, function.output_schema)
                succeeded = True

                # Update execution record
                execution.status = ExecutionStatus.COMPLETED
                execution.output_data = result
                # An earlier failed attempt's note ("attempt 1 of 4 failed")
                execution.error = None
                execution.traceback = None
                execution.completed_at = datetime.utcnow()
                execution.duration_ms = duration_ms

                await db.commit()

                # The run succeeded and is committed: whatever goes wrong in
                # the bookkeeping below is no reason to fail it, let alone
                # run the function again.
                try:
                    # Log execution completion to Redis
                    await clickhouse_logger.log_execution_end(
                        execution_id, "completed", result, None, duration_ms
                    )

                    if callback_url:
                        await _fire_callback(
                            db=db,
                            execution=execution,
                            callback_url=callback_url,
                            user_id=user_id,
                            user_email=user_email,
                            status="success",
                            result=result,
                            error=None,
                            trigger_id=trigger_id,
                            function_namespace=function_namespace,
                            function_name=function_name,
                        )

                    if execution.batch_id is not None:
                        from app.services import batch_service
                        await batch_service.on_execution_terminated(
                            db=db, batch_id=execution.batch_id,
                        )
                except Exception:
                    logger.error(
                        f"Bookkeeping after the successful execution {execution_id} failed",
                        exc_info=True,
                    )

                return result

            except SharedPoolSaturated:
                # Backpressure, not failure ("fail-fast, retryable" by
                # design): leave the row PENDING so the queue worker's
                # deferred retry finds it un-failed, and skip the failure
                # callback/logging entirely.
                execution.status = ExecutionStatus.PENDING
                await db.commit()
                raise
            except Exception as e:
                # Infrastructure: the executor said the code never started, or
                # a connection dropped before dispatch (e.g. the admission
                # gate's Redis).
                transient = isinstance(e, ExecutorUnavailable) or (
                    not dispatched and is_transient_infra_error(e)
                )
                if succeeded:
                    # The code ran to a valid result; only saving it failed.
                    # Running it again would repeat its side effects.
                    kind, max_retries = FUNCTION, 0
                elif transient:
                    kind, max_retries = INFRASTRUCTURE, settings.queue_max_retries
                elif dispatched and not isinstance(e, SchemaValidationError):
                    kind, max_retries = FUNCTION, function.max_retries or 0
                else:
                    kind, max_retries = FUNCTION, 0  # deterministic: retrying changes nothing
                prior = (prior_failures or {}).get(kind, 0)
                attempt = prior + 1
                # Prefer the function's real traceback from the container; fall
                # back to the backend stack for errors raised outside the function
                # (schema validation, db, container plumbing, etc.).
                error_traceback = getattr(e, "container_traceback", None) or traceback.format_exc()
                execution.traceback = error_traceback

                if prior_failures is not None and prior < max_retries:
                    # Not terminal yet: back in the queue, the failure noted.
                    # The retry must not hinge on this write: the session
                    # may have lost its connection with the failure itself.
                    note = f"Attempt {attempt} of {max_retries + 1} failed, retrying: {e}"
                    execution.status = ExecutionStatus.PENDING
                    execution.error = note
                    try:
                        await db.commit()
                    except Exception:
                        with contextlib.suppress(Exception):
                            await db.rollback()
                        await _save_retry_note(execution_id, note, error_traceback)
                    raise ExecutionRetryScheduled(
                        str(e),
                        kind=kind,
                        attempt=attempt,
                        max_attempts=max_retries + 1,
                    ) from e

                # Retryable, but out of attempts: the queue dead-letters it.
                dead_letter = prior_failures is not None and (transient or max_retries > 0)

                # Update execution record with error
                execution_error = str(e)
                if dead_letter:
                    execution_error += dead_letter_note(sum(prior_failures.values()) + 1)
                execution.status = ExecutionStatus.FAILED
                execution.error = execution_error
                execution.completed_at = datetime.utcnow()

                err = FunctionExecutionError(f"Function execution failed: {e}")
                err.transient = transient
                err.dead_letter = dead_letter
                err.recorded = True

                try:
                    await db.commit()
                except Exception:
                    # The session went down with the failure: record it
                    # through a fresh one (row, log, callback, batch).
                    logger.warning(
                        f"Recording the failure of {execution_id} in a fresh session", exc_info=True
                    )
                    with contextlib.suppress(Exception):
                        await db.rollback()
                    await self.record_failure(
                        execution_id=execution_id,
                        error=execution_error,
                        user_id=user_id,
                        trigger_id=trigger_id,
                        function_namespace=function_namespace,
                        function_name=function_name,
                        callback_url=callback_url,
                    )
                    raise err from e

                # The failure is committed: an error in the bookkeeping below
                # must not replace `err`, whose flags tell the queue worker
                # whether to dead-letter the job.
                try:
                    # Log execution failure to Redis
                    await clickhouse_logger.log_execution_end(
                        execution_id, "failed", None, str(e), None
                    )

                    if callback_url:
                        await _fire_callback(
                            db=db,
                            execution=execution,
                            callback_url=callback_url,
                            user_id=user_id,
                            user_email=user_email,
                            status="failure",
                            result=None,
                            error=str(e),
                            trigger_id=trigger_id,
                            function_namespace=function_namespace,
                            function_name=function_name,
                        )

                    if execution.batch_id is not None:
                        from app.services import batch_service
                        await batch_service.on_execution_terminated(
                            db=db, batch_id=execution.batch_id,
                        )
                except Exception:
                    logger.error(
                        f"Bookkeeping after the failed execution {execution_id} failed",
                        exc_info=True,
                    )

                raise err

    async def resume_execution(
        self,
        execution_id: str,
        resume_value: Any,
    ) -> dict[str, Any]:
        """
        Resume a paused (AWAITING_INPUT) execution with a human-provided value.

        Routing and persistence live here; the actual resume mechanism is
        delegated to the executor that produced the pause, via the opaque
        handle stored on the execution. Bypasses the queue.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Execution).where(Execution.execution_id == execution_id)
            )
            execution = result.scalar_one_or_none()

            if not execution:
                raise FunctionExecutionError(f"Execution {execution_id} not found")

            if execution.status != ExecutionStatus.AWAITING_INPUT:
                raise FunctionExecutionError(
                    f"Execution {execution_id} is not awaiting input (status={execution.status})"
                )

            if not execution.container_id:
                raise FunctionExecutionError(
                    f"Execution {execution_id} has no resume handle — cannot resume"
                )

            # container_id holds "role:handle". Treat a bare value (no ":") as a
            # trusted handle, for executions paused before this format existed.
            role, sep, handle = execution.container_id.partition(":")
            if not sep:
                role, handle = "trusted", execution.container_id

            executor = self.sandbox_executor if role == "sandbox" else self.trusted_executor
            if executor is None:
                execution.status = ExecutionStatus.FAILED
                execution.error = (
                    f"{role.capitalize()} execution is disabled; "
                    f"cannot resume this execution."
                )
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise FunctionExecutionError(execution.error)

            execution.status = ExecutionStatus.RUNNING
            await db.commit()

            res = await executor.resume(
                handle=handle,
                resume_value=resume_value,
                execution_id=execution_id,
                timeout=settings.function_timeout,
            )

            # Another input() call — stay paused, keep the (re-stamped) handle.
            if res.status is ResultStatus.AWAITING_INPUT:
                execution.status = ExecutionStatus.AWAITING_INPUT
                execution.input_prompt = res.prompt
                execution.container_id = f"{role}:{res.handle}"
                await db.commit()
                return {
                    "status": "awaiting_input",
                    "execution_id": execution_id,
                    "prompt": execution.input_prompt,
                }

            if res.status is ResultStatus.FAILED:
                execution.status = ExecutionStatus.FAILED
                execution.error = res.error or "Unknown error"
                execution.traceback = res.traceback
                execution.completed_at = datetime.utcnow()
                await db.commit()
                raise FunctionExecutionError(res.error or "Unknown error")

            # Completed
            execution.status = ExecutionStatus.COMPLETED
            execution.output_data = res.result
            execution.completed_at = datetime.utcnow()
            execution.duration_ms = res.duration_ms
            execution.container_id = None
            await db.commit()

            await clickhouse_logger.log_execution_end(
                execution_id, "completed", res.result, None, res.duration_ms
            )

            return res.result

    async def enqueue_function(
        self,
        function_namespace: str,
        function_name: str,
        input_data: dict[str, Any],
        execution_id: str,
        trigger_type: str,
        trigger_id: str,
        user_id: str,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Enqueue a function for execution via the job queue.

        Creates an Execution record with PENDING status and dispatches to the queue.
        Returns immediately with job_id and execution_id.
        """
        from app.services.queue_service import queue_service

        # Create Execution record with PENDING status
        async with AsyncSessionLocal() as db:
            execution = Execution(
                user_id=user_id,
                execution_id=execution_id,
                function_name=function_name,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
                chat_id=chat_id,
                status=ExecutionStatus.PENDING,
                input_data=input_data,
            )
            db.add(execution)
            await db.commit()

        # Enqueue via queue service
        job_id = await queue_service.enqueue_function(
            function_namespace=function_namespace,
            function_name=function_name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            user_id=user_id,
            chat_id=chat_id,
        )

        return {
            "status": "queued",
            "execution_id": execution_id,
            "job_id": job_id,
        }

    async def record_failure(
        self,
        *,
        execution_id: str,
        error: str,
        user_id: str,
        trigger_id: str,
        function_namespace: str,
        function_name: str,
        callback_url: str | None = None,
    ) -> None:
        """The failure bookkeeping for a queued execution that failed before
        or outside `execute_function`'s own (pool saturation that never let
        up, the database unreachable while preparing, the function gone):
        row FAILED, execution log, callback, batch. A row that already
        reached a terminal state is left alone. Best effort: never raises."""
        try:
            async with AsyncSessionLocal() as db:
                execution = (
                    await db.execute(select(Execution).where(Execution.execution_id == execution_id))
                ).scalar_one_or_none()
                if execution is None or execution.status not in (
                    ExecutionStatus.PENDING,
                    ExecutionStatus.RUNNING,
                ):
                    return
                execution.status = ExecutionStatus.FAILED
                execution.error = error
                execution.completed_at = datetime.utcnow()
                await db.commit()

                await clickhouse_logger.log_execution_end(execution_id, "failed", None, error, None)

                if callback_url:
                    from app.models.user import User

                    user = (
                        await db.execute(select(User).where(User.id == user_id))
                    ).scalar_one_or_none()
                    await _fire_callback(
                        db=db,
                        execution=execution,
                        callback_url=callback_url,
                        user_id=user_id,
                        user_email=user.email if user else "unknown@unknown.com",
                        status="failure",
                        result=None,
                        error=error,
                        trigger_id=trigger_id,
                        function_namespace=function_namespace,
                        function_name=function_name,
                    )

                if execution.batch_id is not None:
                    from app.services import batch_service

                    await batch_service.on_execution_terminated(db=db, batch_id=execution.batch_id)
        except Exception:
            logger.error(f"Could not record the failure of execution {execution_id}", exc_info=True)

    def clear_cache(self):
        """Clear function cache."""
        self.functions_cache.clear()


# Global executor instance
executor = FunctionExecutor()
