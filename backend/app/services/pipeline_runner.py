"""Pipeline runner — one async runner, two entry points (queued job / inline sync).

See backend/docs/adrs/2026-07-28-pipelines-triggers-and-linear-steps.md.

Key invariants:
- The cursor commits ONLY when the whole run succeeds (at-least-once delivery;
  a failed run holds the bookmark).
- Single-flight per run scope (pipeline, or pipeline+user for perUser) via a
  Redis lock; queued fires that hit a held lock leave a coalesce flag that the
  lock holder re-enqueues once on completion. Sync callers get PipelineBusyError.
- One user's failure never blocks or fails other users' runs (perUser isolation).
- Never dispatch a step with a half-resolved input: mapping errors fail the run
  before the step executes.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import jsonschema
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.connector import Connector
from app.models.database_connection import DatabaseConnection
from app.models.execution import TriggerType
from app.models.pipeline import Pipeline, PipelineCursor, PipelineRun
from app.models.query import Query
from app.models.user import User
from app.services import pipeline_mapping as pm

logger = logging.getLogger(__name__)

LOCK_PREFIX = "sinas:pipeline:lock:"
PENDING_PREFIX = "sinas:pipeline:pending:"
LOCK_MARGIN_SECONDS = 600
PENDING_TTL = 3600

# Release the lock only if we still own it (value = run_id).
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class PipelineBusyError(Exception):
    """A sync run hit the single-flight lock: another run is active."""

    def __init__(self, active_run_id: Optional[str]):
        self.active_run_id = active_run_id
        super().__init__(f"Pipeline run already in progress (run_id={active_run_id})")


class StepError(Exception):
    """A step failed after exhausting its retries."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_key(pipeline: Pipeline, user_id: str) -> str:
    if pipeline.per_user:
        return f"{LOCK_PREFIX}{pipeline.id}:{user_id}"
    return f"{LOCK_PREFIX}{pipeline.id}"


def _pending_key(pipeline: Pipeline, user_id: str) -> str:
    if pipeline.per_user:
        return f"{PENDING_PREFIX}{pipeline.id}:{user_id}"
    return f"{PENDING_PREFIX}{pipeline.id}"


def _backoff_delay(attempt: int, strategy: str) -> float:
    if strategy == "exponential":
        return min(2**attempt * 0.5, 30.0)
    if strategy == "linear":
        return min((attempt + 1) * 1.0, 30.0)
    return 0.0


def _split_ref(ref: str) -> tuple[str, str]:
    namespace, name = ref.split("/", 1)
    return namespace, name


def _summarize(value: Any, limit: int = 2000) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:
        text = str(value)
    return text[:limit]


# ---------------------------------------------------------------------------
# Step executors. Each returns the step's context output on success and raises
# StepError on failure. They receive the *resolved* input.
# ---------------------------------------------------------------------------


async def _execute_connector_step(
    step: dict[str, Any], step_input: Any, *, user_id: str, user_token: Optional[str]
) -> Any:
    from app.services.connector_service import ConnectorAuthError, connector_service

    namespace, name = _split_ref(step["connector"])
    operation = step["operation"]

    async with AsyncSessionLocal() as db:
        connector = await Connector.get_by_name(db, namespace, name)
        if not connector or not connector.is_active:
            raise StepError(f"Connector '{namespace}/{name}' not found or inactive")
        params = step_input if isinstance(step_input, dict) else {}
        try:
            result = await connector_service.execute_operation(
                db=db,
                connector=connector,
                operation_name=operation,
                parameters=params,
                user_token=user_token,
                user_id=user_id,
            )
        except ConnectorAuthError as e:
            raise StepError(str(e))
        except ValueError as e:
            raise StepError(str(e))

    status_code = result.get("status_code", 0)
    allowed = step.get("allowStatuses") or []
    if not (200 <= status_code < 300) and status_code not in allowed:
        raise StepError(
            f"Connector {namespace}/{name}/{operation} returned {status_code}: "
            f"{_summarize(result.get('body'), 500)}"
        )
    return {
        "statusCode": status_code,
        "body": result.get("body"),
        "elapsedMs": result.get("elapsed_ms"),
    }


async def _execute_function_step(
    step: dict[str, Any], step_input: Any, *,
    user_id: str, run_id: str, depth: int, summary: dict[str, Any],
) -> Any:
    from app.services.queue_service import queue_service

    namespace, name = _split_ref(step["function"])
    execution_id = str(uuid.uuid4())
    summary["executionId"] = execution_id
    try:
        return await queue_service.enqueue_and_wait(
            function_namespace=namespace,
            function_name=name,
            input_data=step_input if isinstance(step_input, dict) else {"input": step_input},
            execution_id=execution_id,
            trigger_type=TriggerType.PIPELINE.value,
            trigger_id=run_id,
            user_id=user_id,
            depth=depth,
        )
    except TimeoutError as e:
        raise StepError(f"Function {namespace}/{name} timed out: {e}")
    except Exception as e:
        raise StepError(f"Function {namespace}/{name} failed: {e}")


async def _execute_query_step(
    step: dict[str, Any], step_input: Any, *, user_id: str
) -> Any:
    from app.services.database_pool import DatabasePoolManager

    namespace, name = _split_ref(step["query"])
    async with AsyncSessionLocal() as db:
        query = await Query.get_by_name(db, namespace, name)
        if not query or not query.is_active:
            raise StepError(f"Query '{namespace}/{name}' not found or inactive")

        params = dict(step_input) if isinstance(step_input, dict) else {}
        params.setdefault("user_id", str(user_id))
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user:
            params.setdefault("user_email", user.email)

        try:
            result = await DatabasePoolManager.get_instance().execute_query(
                db=db,
                connection_id=str(query.database_connection_id),
                sql=query.sql,
                params=params,
                operation=query.operation,
                timeout_ms=query.timeout_ms,
                max_rows=query.max_rows,
            )
        except Exception as e:
            raise StepError(f"Query {namespace}/{name} failed: {e}")

    return {
        "rows": result.get("rows"),
        "rowCount": result.get("row_count"),
        "affectedRows": result.get("affected_rows"),
    }


async def _execute_agent_step(
    step: dict[str, Any], step_input: Any, *,
    user_id: str, user_token: str, run_id: str, agent_depth: int,
    pipeline_label: str, summary: dict[str, Any],
) -> Any:
    from app.models.agent import Agent
    from app.models.chat import Chat
    from app.services.queue_service import queue_service
    from app.services.stream_relay import stream_relay

    namespace, name = _split_ref(step["agent"])

    async with AsyncSessionLocal() as db:
        agent = await Agent.get_by_name(db, namespace, name)
        if not agent:
            raise StepError(f"Agent '{namespace}/{name}' not found or inactive")

        input_data = step_input if isinstance(step_input, dict) else {}
        chat = Chat(
            user_id=user_id,
            agent_id=agent.id,
            agent_namespace=agent.namespace,
            agent_name=agent.name,
            title=f"pipeline:{pipeline_label} — {_now().strftime('%Y-%m-%d %H:%M')}",
            chat_metadata={"agent_input": input_data} if input_data else None,
            job_timeout=agent.default_job_timeout,
        )
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        chat_id = str(chat.id)
        output_schema = agent.output_schema or {}
        agent_timeout = agent.default_job_timeout or settings.agent_delegate_timeout

    summary["chatId"] = chat_id

    # Message: explicit template/expression, else the JSON-serialized input.
    if "message" in step or "message.$" in step:
        message = step.get("message")
        if message is None:
            message = step_input  # resolved from message.$ upstream
        if not isinstance(message, str):
            message = json.dumps(message, default=str)
    else:
        message = json.dumps(input_data, default=str) if input_data else "Run your task."

    channel_id = str(uuid.uuid4())
    await queue_service.enqueue_agent_message(
        chat_id=chat_id,
        user_id=user_id,
        user_token=user_token,
        content=message,
        channel_id=channel_id,
        agent=f"{namespace}/{name}",
        trigger_type=TriggerType.PIPELINE.value.lower(),
        depth=agent_depth,
    )

    final_content = ""
    got_terminal = False
    async for event in stream_relay.subscribe(channel_id, timeout=agent_timeout):
        if event.get("content"):
            final_content += event["content"]
        if event.get("type") in ("done", "error"):
            got_terminal = True
            if event.get("type") == "error":
                raise StepError(f"Agent {namespace}/{name} failed: {event.get('error', 'unknown error')}")
            break

    if not got_terminal:
        raise StepError(f"Agent {namespace}/{name} did not respond within {agent_timeout}s")

    if output_schema.get("properties"):
        try:
            parsed = json.loads(final_content)
        except json.JSONDecodeError as e:
            raise StepError(
                f"Agent {namespace}/{name} reply is not valid JSON despite outputSchema: {e}. "
                f"Reply started with: {final_content[:200]!r}"
            )
        try:
            jsonschema.validate(instance=parsed, schema=output_schema)
        except jsonschema.ValidationError as e:
            raise StepError(f"Agent {namespace}/{name} reply violates outputSchema: {e.message}")
        return parsed
    return final_content


def _quote_table(table: str) -> str:
    parts = table.split(".")
    return ".".join(f'"{p}"' for p in parts)


async def _execute_load_step(
    step: dict[str, Any], *, context: dict[str, Any], user_id: str, per_user: bool
) -> Any:
    from app.services.database_pool import DatabasePoolManager

    items = pm.evaluate_expression(step["items.$"], context)
    if items is None:
        items = []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise StepError("load: items.$ must resolve to an array (or single object)")

    async with AsyncSessionLocal() as db:
        conn_model = await DatabaseConnection.get_by_name(db, step["connection"])
        if not conn_model:
            raise StepError(f"Database connection '{step['connection']}' not found")
        pool = await DatabasePoolManager.get_instance().get_pool(db, str(conn_model.id))

    table_sql = _quote_table(step["table"])
    rows: list[tuple] = []
    for i, item in enumerate(items):
        pk = pm.evaluate_expression(step["primaryKey.$"], {**context, "item": item})
        if pk is None:
            raise StepError(f"load: primaryKey.$ resolved to null for item {i}")
        rows.append((str(pk), json.dumps(item, default=str)))

    async with pool.acquire() as conn:
        async with conn.transaction():
            if per_user:
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table_sql} ("
                    f"user_id uuid NOT NULL, pk text NOT NULL, payload jsonb NOT NULL, "
                    f"synced_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (user_id, pk))"
                )
                await conn.executemany(
                    f"INSERT INTO {table_sql} (user_id, pk, payload) VALUES ($1, $2, $3::jsonb) "
                    f"ON CONFLICT (user_id, pk) DO UPDATE SET payload = EXCLUDED.payload, synced_at = now()",
                    [(uuid.UUID(str(user_id)), pk, payload) for pk, payload in rows],
                )
            else:
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {table_sql} ("
                    f"pk text PRIMARY KEY, payload jsonb NOT NULL, "
                    f"synced_at timestamptz NOT NULL DEFAULT now())"
                )
                await conn.executemany(
                    f"INSERT INTO {table_sql} (pk, payload) VALUES ($1, $2::jsonb) "
                    f"ON CONFLICT (pk) DO UPDATE SET payload = EXCLUDED.payload, synced_at = now()",
                    rows,
                )

    return {"upserted": len(rows), "table": step["table"]}


# ---------------------------------------------------------------------------
# Cursor state
# ---------------------------------------------------------------------------


async def _read_cursor(pipeline: Pipeline, user_id: str) -> Optional[str]:
    if not pipeline.per_user:
        return pipeline.cursor_value
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(PipelineCursor).where(
                    PipelineCursor.pipeline_id == pipeline.id,
                    PipelineCursor.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        return row.cursor_value if row else None


async def _finalize_success(
    pipeline: Pipeline, user_id: str, new_cursor: Optional[str]
) -> None:
    """Commit the cursor (if any) and reset failure counters for the run scope."""
    async with AsyncSessionLocal() as db:
        if pipeline.per_user:
            row = (
                await db.execute(
                    select(PipelineCursor).where(
                        PipelineCursor.pipeline_id == pipeline.id,
                        PipelineCursor.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = PipelineCursor(pipeline_id=pipeline.id, user_id=user_id)
                db.add(row)
            if new_cursor is not None:
                row.cursor_value = new_cursor
            row.consecutive_failures = 0
            row.last_error = None
        else:
            values: dict[str, Any] = {"consecutive_failures": 0, "error_message": None}
            if new_cursor is not None:
                values["cursor_value"] = new_cursor
            await db.execute(update(Pipeline).where(Pipeline.id == pipeline.id).values(**values))
        await db.commit()


async def _finalize_failure(pipeline: Pipeline, user_id: str, error: str) -> None:
    """Hold the cursor, bump failure counters, apply auto-disable/skip policy."""
    async with AsyncSessionLocal() as db:
        if pipeline.per_user:
            row = (
                await db.execute(
                    select(PipelineCursor).where(
                        PipelineCursor.pipeline_id == pipeline.id,
                        PipelineCursor.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = PipelineCursor(pipeline_id=pipeline.id, user_id=user_id)
                db.add(row)
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.last_error = error[:2000]
        else:
            db_pipeline = (
                await db.execute(select(Pipeline).where(Pipeline.id == pipeline.id))
            ).scalar_one_or_none()
            if db_pipeline:
                db_pipeline.consecutive_failures = (db_pipeline.consecutive_failures or 0) + 1
                db_pipeline.error_message = error[:2000]
                threshold = db_pipeline.disable_after_failures
                if threshold and db_pipeline.consecutive_failures >= threshold:
                    db_pipeline.is_active = False
                    logger.warning(
                        f"Pipeline {db_pipeline.namespace}/{db_pipeline.name} deactivated "
                        f"after {db_pipeline.consecutive_failures} consecutive failures"
                    )
        await db.commit()


async def reset_user_failure_state(
    db, user_id: str, *, connector_id: Any = None, secret_name: Optional[str] = None
) -> None:
    """Reset per-user failure counters when a user re-credentials.

    Called from the OAuth callback (fresh token stored, pass connector_id) and
    private-secret create/update (pass secret_name) — the natural recovery
    points for skip-until-re-credential. Matches pipelines whose
    perUser.connector resolves to the affected connector(s). Best-effort:
    failures here must never break the credential operation itself.
    """
    try:
        refs: set[str] = set()
        if connector_id is not None:
            connector = (
                await db.execute(select(Connector).where(Connector.id == connector_id))
            ).scalar_one_or_none()
            if connector:
                refs.add(f"{connector.namespace}/{connector.name}")
        if secret_name:
            connectors = (
                await db.execute(select(Connector).where(Connector.is_active == True))  # noqa: E712
            ).scalars().all()
            refs.update(
                f"{c.namespace}/{c.name}"
                for c in connectors
                if (c.auth or {}).get("secret") == secret_name
            )
        if not refs:
            return

        pipelines = (
            await db.execute(select(Pipeline).where(Pipeline.per_user.isnot(None)))
        ).scalars().all()
        ids = [p.id for p in pipelines if (p.per_user or {}).get("connector") in refs]
        if not ids:
            return
        await db.execute(
            update(PipelineCursor)
            .where(PipelineCursor.pipeline_id.in_(ids), PipelineCursor.user_id == user_id)
            .values(consecutive_failures=0, last_error=None)
        )
    except Exception:
        logger.exception("Failed to reset pipeline failure state on re-credential")


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


async def run_pipeline(
    pipeline_id: str,
    run_input: Optional[dict[str, Any]],
    *,
    trigger_type: str,
    trigger_id: Optional[str],
    user_id: str,
    user_token: str,
    run_id: Optional[str] = None,
    exec_depth: int = 0,
    agent_depth: int = 0,
    sync: bool = False,
) -> dict[str, Any]:
    """Execute one pipeline run for one user scope. Returns the run outcome:

        {"run_id", "status", "output", "error", "steps", "duration_ms"}

    status: succeeded | failed | timed_out | skipped (queued fire coalesced).
    Raises PipelineBusyError for sync callers when the single-flight lock is held.
    """
    async with AsyncSessionLocal() as db:
        pipeline = (
            await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
        ).scalar_one_or_none()
        if pipeline is None:
            raise ValueError(f"Pipeline {pipeline_id} not found")
        if not pipeline.is_active:
            raise ValueError(f"Pipeline {pipeline.namespace}/{pipeline.name} is inactive")
        db.expunge(pipeline)

    label = f"{pipeline.namespace}/{pipeline.name}"
    # Queued runs arrive with a pre-allocated id (the one their enqueue
    # response promised); sync runs mint their own.
    run_id = run_id or str(uuid.uuid4())
    redis = await get_redis()

    # --- single-flight ---
    lock_key = _lock_key(pipeline, user_id)
    lock_ttl = pipeline.sync_timeout_seconds + LOCK_MARGIN_SECONDS
    use_lock = pipeline.effective_concurrency() == "single"
    if use_lock:
        acquired = await redis.set(lock_key, run_id, nx=True, ex=lock_ttl)
        if not acquired:
            active = await redis.get(lock_key)
            if sync:
                raise PipelineBusyError(active)
            # Coalesce: remember the latest fire so the lock holder re-runs once.
            await redis.set(
                _pending_key(pipeline, user_id),
                json.dumps({
                    "input": run_input,
                    "trigger_type": trigger_type,
                    "trigger_id": trigger_id,
                    "user_id": str(user_id),
                }),
                ex=PENDING_TTL,
            )
            logger.info(f"Pipeline {label}: run in progress ({active}), coalesced this fire")
            return {"run_id": run_id, "status": "skipped", "output": None,
                    "error": None, "steps": [], "duration_ms": 0}

    started = _now()
    t0 = time.monotonic()
    cursor_before = await _read_cursor(pipeline, user_id)

    # Create the run record up front (it doubles as the dead-letter record).
    async with AsyncSessionLocal() as db:
        db.add(PipelineRun(
            pipeline_id=pipeline.id,
            run_id=run_id,
            user_id=user_id,
            trigger_type=TriggerType(trigger_type),
            trigger_id=trigger_id,
            status="running",
            input=run_input,
            steps=[],
            cursor_before=cursor_before,
            started_at=started,
        ))
        await db.commit()

    context: dict[str, Any] = {
        "input": run_input or {},
        "steps": {},
        "cursor": cursor_before,
        "run": {
            "id": run_id,
            "triggerType": trigger_type,
            "firedAt": started.isoformat(),
            "userId": str(user_id),
        },
    }
    step_summaries: list[dict[str, Any]] = []
    status = "failed"
    error: Optional[str] = None
    output: Any = None
    new_cursor: Optional[str] = None

    try:
        # Validate run input against the pipeline's input schema.
        if pipeline.input_schema and pipeline.input_schema.get("properties"):
            try:
                jsonschema.validate(instance=run_input or {}, schema=pipeline.input_schema)
            except jsonschema.ValidationError as e:
                raise StepError(f"Run input validation failed: {e.message}")

        cursor_step_name = None
        cursor_path = None

        for step in pipeline.steps:
            name = step["name"]
            step_type = step["type"]
            summary: dict[str, Any] = {
                "name": name, "type": step_type, "status": "running",
                "startedAt": _now().isoformat(),
            }
            step_summaries.append(summary)
            step_t0 = time.monotonic()

            # Resolve input (mapping failure must fail the run BEFORE dispatch).
            try:
                step_input = pm.resolve_field(step, "input", context, default={})
                if step_type == "agent" and "message.$" in step:
                    # Pre-resolve message.$ so the executor sees the final string.
                    step = {**step, "message": pm.evaluate_expression(step["message.$"], context)}
            except Exception as e:
                raise StepError(f"Step '{name}': input mapping failed: {e}")

            # Cursor injection
            cursor_cfg = step.get("cursor")
            if cursor_cfg:
                cursor_step_name = name
                cursor_path = cursor_cfg["path"]
                param = cursor_cfg["param"]
                if isinstance(step_input, dict) and param not in step_input:
                    value = context["cursor"]
                    if value is None:
                        if "initial.$" in cursor_cfg:
                            value = pm.evaluate_expression(cursor_cfg["initial.$"], context)
                        elif "initial" in cursor_cfg:
                            value = cursor_cfg["initial"]
                    if value is not None:
                        step_input[param] = value

            retry_cfg = step.get("retry") or {}
            max_attempts = retry_cfg.get("maxAttempts", 1)
            if step_type == "agent":
                max_attempts = 1  # agent steps are never retried by the runner (v1)
            backoff = retry_cfg.get("backoff", "none")

            result: Any = None
            last_err: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    if step_type == "connector":
                        result = await _execute_connector_step(
                            step, step_input, user_id=user_id, user_token=user_token
                        )
                    elif step_type == "function":
                        result = await _execute_function_step(
                            step, step_input, user_id=user_id, run_id=run_id,
                            depth=exec_depth, summary=summary,
                        )
                    elif step_type == "query":
                        result = await _execute_query_step(step, step_input, user_id=user_id)
                    elif step_type == "agent":
                        result = await _execute_agent_step(
                            step, step_input, user_id=user_id, user_token=user_token,
                            run_id=run_id, agent_depth=agent_depth,
                            pipeline_label=label, summary=summary,
                        )
                    elif step_type == "load":
                        result = await _execute_load_step(
                            step, context=context, user_id=user_id,
                            per_user=bool(pipeline.per_user),
                        )
                    else:  # unreachable post-validation
                        raise StepError(f"Unknown step type '{step_type}'")
                    last_err = None
                    break
                except StepError as e:
                    last_err = e
                    if attempt < max_attempts - 1:
                        delay = _backoff_delay(attempt, backoff)
                        logger.warning(
                            f"Pipeline {label} step '{name}' attempt {attempt + 1}/{max_attempts} "
                            f"failed: {e}; retrying in {delay:.1f}s"
                        )
                        if delay > 0:
                            await asyncio.sleep(delay)

            summary["durationMs"] = round((time.monotonic() - step_t0) * 1000)
            if last_err is not None:
                summary["status"] = "failed"
                summary["error"] = str(last_err)[:2000]
                raise StepError(f"Step '{name}': {last_err}")

            summary["status"] = "succeeded"
            context["steps"][name] = {"output": result}

            # Candidate cursor: read right after the cursor step succeeds.
            if cursor_cfg and cursor_path:
                candidate = pm.evaluate_expression(cursor_path, {**context, "steps": context["steps"]})
                if candidate is not None:
                    new_cursor = str(candidate)
                # None → leave the bookmark unchanged (never regress on "no data").

        # Final output
        if pipeline.output_mapping:
            output = pm.resolve_field(pipeline.output_mapping, "output", context)
        elif pipeline.steps:
            last_name = pipeline.steps[-1]["name"]
            output = context["steps"].get(last_name, {}).get("output")

        status = "succeeded"

    except StepError as e:
        error = str(e)
        logger.error(f"Pipeline {label} run {run_id} failed: {error}")
    except asyncio.CancelledError:
        # Sync caller's wait_for timed out (or the job was cancelled).
        status = "timed_out"
        running = [s["name"] for s in step_summaries if s.get("status") == "running"]
        error = (
            f"Run timed out after {pipeline.sync_timeout_seconds}s"
            + (f" while executing step '{running[0]}'" if running else "")
            + f". Completed steps had side effects; see run {run_id}."
        )
        for s in step_summaries:
            if s.get("status") == "running":
                s["status"] = "timed_out"
        await _persist_outcome(
            pipeline, user_id, run_id, status, error, None, step_summaries,
            new_cursor=None, t0=t0, use_lock=use_lock, lock_key=lock_key, redis=redis,
        )
        raise
    except Exception as e:  # defensive: mapping/DB bugs must still finalize the run
        error = f"Internal pipeline error: {e}"
        logger.exception(f"Pipeline {label} run {run_id} internal error")

    duration_ms = await _persist_outcome(
        pipeline, user_id, run_id, status, error, output, step_summaries,
        new_cursor=new_cursor if status == "succeeded" else None,
        t0=t0, use_lock=use_lock, lock_key=lock_key, redis=redis,
    )

    return {
        "run_id": run_id,
        "status": status,
        "output": output if status == "succeeded" else None,
        "error": error,
        "steps": step_summaries,
        "duration_ms": duration_ms,
    }


async def _persist_outcome(
    pipeline: Pipeline,
    user_id: str,
    run_id: str,
    status: str,
    error: Optional[str],
    output: Any,
    step_summaries: list[dict[str, Any]],
    *,
    new_cursor: Optional[str],
    t0: float,
    use_lock: bool,
    lock_key: str,
    redis,
) -> int:
    """Finalize the run record, commit/hold cursor state, release the lock, and
    re-enqueue one coalesced fire if any arrived while we ran."""
    duration_ms = round((time.monotonic() - t0) * 1000)
    try:
        if status == "succeeded":
            await _finalize_success(pipeline, user_id, new_cursor)
        else:
            await _finalize_failure(pipeline, user_id, error or status)

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(PipelineRun)
                .where(PipelineRun.run_id == run_id)
                .values(
                    status=status,
                    error=error,
                    output=output if status == "succeeded" else None,
                    steps=step_summaries,
                    cursor_after=new_cursor,
                    completed_at=_now(),
                    duration_ms=duration_ms,
                )
            )
            await db.commit()
    finally:
        if use_lock:
            try:
                await redis.eval(_RELEASE_LUA, 1, lock_key, run_id)
                pending_key = _pending_key(pipeline, user_id)
                pending = await redis.get(pending_key)
                if pending:
                    await redis.delete(pending_key)
                    data = json.loads(pending)
                    from app.services.queue_service import queue_service

                    await queue_service.enqueue_pipeline_run(
                        pipeline_id=str(pipeline.id),
                        run_input=data.get("input"),
                        trigger_type=data.get("trigger_type", TriggerType.API.value),
                        trigger_id=data.get("trigger_id"),
                        user_id=data.get("user_id", str(user_id)),
                    )
                    logger.info(
                        f"Pipeline {pipeline.namespace}/{pipeline.name}: re-enqueued coalesced fire"
                    )
            except Exception:
                logger.exception("Pipeline lock release/coalesce failed")
    return duration_ms


# ---------------------------------------------------------------------------
# Fan-out ("fire"): expand a trigger firing into per-user runs.
# ---------------------------------------------------------------------------


async def enumerate_connected_users(db, pipeline: Pipeline) -> list[str]:
    """Users connected to the perUser source connector, minus skip-listed ones.

    Enumeration depends on the connector's auth type:
    - oauth2_authorization_code → users with a connector_oauth_tokens row;
    - secret-based auth → users holding a PRIVATE Secret named auth.secret;
    - none/sinas_token → rejected at validation, empty here.
    """
    from app.models.connector_oauth_token import ConnectorOAuthToken
    from app.models.secret import Secret

    per_user = pipeline.per_user or {}
    ref = per_user.get("connector", "")
    if "/" not in ref:
        return []
    namespace, name = _split_ref(ref)
    connector = await Connector.get_by_name(db, namespace, name)
    if not connector:
        logger.warning(f"perUser connector '{ref}' not found for pipeline {pipeline.namespace}/{pipeline.name}")
        return []

    auth_type = (connector.auth or {}).get("type", "none")
    if auth_type == "oauth2_authorization_code":
        rows = await db.execute(
            select(ConnectorOAuthToken.user_id).where(
                ConnectorOAuthToken.connector_id == connector.id
            )
        )
        user_ids = [str(r[0]) for r in rows.all()]
    elif auth_type in ("bearer", "basic", "api_key", "oauth2_client_credentials"):
        secret_name = (connector.auth or {}).get("secret")
        if not secret_name:
            return []
        rows = await db.execute(
            select(Secret.user_id).where(
                Secret.name == secret_name, Secret.visibility == "private"
            )
        )
        user_ids = [str(r[0]) for r in rows.all() if r[0] is not None]
    else:
        return []

    # Skip users past the per-user failure threshold (until they re-credential).
    threshold = per_user.get("disableAfterFailures")
    if threshold and user_ids:
        rows = await db.execute(
            select(PipelineCursor.user_id).where(
                PipelineCursor.pipeline_id == pipeline.id,
                PipelineCursor.consecutive_failures >= threshold,
            )
        )
        skipped = {str(r[0]) for r in rows.all()}
        if skipped:
            logger.info(
                f"Pipeline {pipeline.namespace}/{pipeline.name}: skipping "
                f"{len(skipped & set(user_ids))} user(s) past failure threshold"
            )
            user_ids = [u for u in user_ids if u not in skipped]

    return user_ids


async def fire_pipeline(
    namespace: str,
    name: str,
    run_input: Optional[dict[str, Any]],
    *,
    trigger_type: str,
    trigger_id: Optional[str],
) -> list[str]:
    """Expand a trigger firing into queued run(s). Returns enqueued run job ids.

    Shared pipelines → one run as the pipeline owner. perUser pipelines → one
    run per connected user, each executing as that user (the run job mints the
    user's JWT worker-side, so tokens never sit in Redis job payloads).
    """
    from app.services.queue_service import queue_service

    async with AsyncSessionLocal() as db:
        pipeline = await Pipeline.get_by_name(db, namespace, name)
        if not pipeline or not pipeline.is_active:
            logger.warning(f"fire_pipeline: '{namespace}/{name}' not found or inactive")
            return []

        if pipeline.per_user:
            user_ids = await enumerate_connected_users(db, pipeline)
        else:
            user_ids = [str(pipeline.user_id)]

        job_ids = []
        for uid in user_ids:
            job_id = await queue_service.enqueue_pipeline_run(
                pipeline_id=str(pipeline.id),
                run_input=run_input,
                trigger_type=trigger_type,
                trigger_id=trigger_id,
                user_id=uid,
            )
            job_ids.append(job_id)

    logger.info(f"Pipeline {namespace}/{name}: fired {len(job_ids)} run(s) ({trigger_type})")
    return job_ids


async def mint_run_token(user_id: str) -> Optional[str]:
    """Mint a short-lived JWT for the run's user (scheduler/webhook pattern)."""
    from app.core.auth import create_access_token

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            return None
        return create_access_token(user_id=str(user.id), email=user.email)
