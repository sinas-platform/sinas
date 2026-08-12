"""Batch service — submit, poll, cancel, and complete batches of function or agent executions.

See ADR 2026-05-14-external-app-bulk-enqueue.md §2.4.
"""
from __future__ import annotations

import json
import logging
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.batch import Batch, BatchKind, BatchStatus
from app.models.chat import Chat, Message
from app.models.execution import Execution, ExecutionStatus, TriggerType
from app.models.agent import Agent
from app.models.function import Function
from app.services.queue_service import queue_service

logger = logging.getLogger(__name__)


class BatchError(Exception):
    """Raised when a batch submission is invalid."""


# Terminal execution statuses — these no longer count toward "pending".
TERMINAL_EXEC_STATUSES = {
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.CANCELLED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _trigger_id(prefix: Optional[str], index: int) -> str:
    if prefix:
        return f"{prefix}:{index}"
    return f"batch:{index}"


def _provider_batch_blockers(agent: Any) -> list[str]:
    """Agent capabilities incompatible with provider-native batch mode.

    A provider batch is a single fire-and-forget LLM call per child — there
    is no runtime to serve tool calls or lifecycle hooks. Preload-only
    skills are fine (they only add system-prompt content).
    """
    blockers: list[str] = []
    if agent.enabled_functions:
        blockers.append("functions")
    if agent.enabled_agents:
        blockers.append("sub-agents")
    if agent.enabled_queries:
        blockers.append("queries")
    if agent.enabled_stores:
        blockers.append("stores")
    if agent.enabled_collections:
        blockers.append("collections")
    if agent.enabled_components:
        blockers.append("components")
    if agent.enabled_connectors:
        blockers.append("connectors")
    if agent.system_tools:
        blockers.append("system tools")
    if agent.hooks and any(agent.hooks.get(k) for k in agent.hooks):
        blockers.append("hooks")
    for skill in agent.enabled_skills or []:
        if isinstance(skill, str) or not skill.get("preload"):
            blockers.append("non-preloaded skills")
            break
    return blockers


async def _resolve_batch_provider(
    db: AsyncSession, agent: Any
) -> tuple[Any, Any, str]:
    """Resolve (provider instance, LLMProvider row, model) for provider-mode
    batches. Raises BatchError when unresolvable or unsupported."""
    from app.models import LLMProvider
    from app.providers import create_provider

    if agent.llm_provider_id:
        row = await db.execute(
            select(LLMProvider).where(
                LLMProvider.id == agent.llm_provider_id, LLMProvider.is_active == True
            )
        )
    else:
        row = await db.execute(
            select(LLMProvider).where(
                LLMProvider.is_default == True, LLMProvider.is_active == True
            )
        )
    provider_row = row.scalar_one_or_none()
    if not provider_row:
        raise BatchError("No active LLM provider found for agent")

    model = agent.model or provider_row.default_model
    if not model:
        raise BatchError(
            f"Agent has no model and provider '{provider_row.name}' has no default_model"
        )

    provider = await create_provider(
        provider_name=provider_row.name, db=db, overrides=agent.provider_overrides
    )
    if not provider.supports_batch:
        raise BatchError(
            f"Provider '{provider_row.name}' ({provider_row.provider_type}) does not "
            f"support batch submission"
        )
    return provider, provider_row, model


# ──────────────── Submission ────────────────


async def submit_function_batch(
    *,
    db: AsyncSession,
    user_id: str,
    namespace: str,
    name: str,
    inputs: list[dict[str, Any]],
    trigger_id_prefix: Optional[str] = None,
    delay_seconds: Optional[int] = None,
    callback_url: Optional[str] = None,
    batch_callback_url: Optional[str] = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Create a batch of function executions. Returns batch_id + execution_ids."""
    if not inputs:
        raise BatchError("inputs must be a non-empty list")
    if len(inputs) > settings.max_batch_size:
        raise BatchError(
            f"batch size {len(inputs)} exceeds MAX_BATCH_SIZE={settings.max_batch_size}"
        )

    function = await Function.get_by_name(db, namespace, name)
    if not function:
        raise BatchError(f"Function '{namespace}/{name}' not found")

    now = _now()
    batch = Batch(
        id=uuid_lib.uuid4(),
        user_id=uuid_lib.UUID(user_id),
        kind=BatchKind.FUNCTION,
        target_namespace=namespace,
        target_name=name,
        total=len(inputs),
        status=BatchStatus.QUEUED,
        trigger_id_prefix=trigger_id_prefix,
        callback_url=callback_url,
        batch_callback_url=batch_callback_url,
        started_at=now,
        created_at=now,
    )
    db.add(batch)
    await db.flush()

    execution_ids: list[str] = []
    for i, input_data in enumerate(inputs):
        execution_id = str(uuid_lib.uuid4())
        execution_ids.append(execution_id)

        execution = Execution(
            user_id=uuid_lib.UUID(user_id),
            execution_id=execution_id,
            function_name=name,
            trigger_type=TriggerType.API,
            trigger_id=_trigger_id(trigger_id_prefix, i),
            status=ExecutionStatus.PENDING,
            input_data=input_data,
            started_at=now,
            batch_id=batch.id,
        )
        db.add(execution)

    await db.commit()

    # Enqueue each child after the rows are committed so the worker can
    # observe them on dequeue.
    for i, (execution_id, input_data) in enumerate(zip(execution_ids, inputs)):
        await queue_service.enqueue_function(
            function_namespace=namespace,
            function_name=name,
            input_data=input_data,
            execution_id=execution_id,
            trigger_type=TriggerType.API.value,
            trigger_id=_trigger_id(trigger_id_prefix, i),
            user_id=user_id,
            delay=delay_seconds,
            callback_url=callback_url,
            depth=depth,
        )

    return {
        "batch_id": str(batch.id),
        "execution_ids": execution_ids,
        "total": batch.total,
        "status": batch.status.value.lower(),
    }


async def submit_agent_batch(
    *,
    db: AsyncSession,
    user_id: str,
    user_token: str,
    namespace: str,
    name: str,
    inputs: list[dict[str, Any]],
    trigger_id_prefix: Optional[str] = None,
    callback_url: Optional[str] = None,
    batch_callback_url: Optional[str] = None,
    execution_mode: str = "queue",
) -> dict[str, Any]:
    """Create a batch of agent invocations. Each input becomes a fresh chat.

    Each `inputs[i]` shape: `{"input_variables": {...}, "message": "..."}`.
    Missing fields default to empty / required keys raise BatchError.

    execution_mode="provider" submits the whole batch to the LLM provider's
    batch API (tool-less agents only); a scheduler job polls it to completion.
    """
    if not inputs:
        raise BatchError("inputs must be a non-empty list")
    if len(inputs) > settings.max_batch_size:
        raise BatchError(
            f"batch size {len(inputs)} exceeds MAX_BATCH_SIZE={settings.max_batch_size}"
        )

    agent = await Agent.get_by_name(db, namespace, name)
    if not agent or not agent.is_active:
        raise BatchError(f"Agent '{namespace}/{name}' not found or inactive")

    for i, item in enumerate(inputs):
        if not isinstance(item, dict) or "message" not in item:
            raise BatchError(f"inputs[{i}] must have a 'message' field")

    provider = provider_row = batch_model = None
    if execution_mode == "provider":
        blockers = _provider_batch_blockers(agent)
        if blockers:
            raise BatchError(
                f"Agent '{namespace}/{name}' cannot run in provider batch mode — "
                f"it uses: {', '.join(blockers)}. Provider batches support only "
                f"tool-less agents (preload-only skills are allowed)."
            )
        provider, provider_row, batch_model = await _resolve_batch_provider(db, agent)
    elif execution_mode != "queue":
        raise BatchError(f"Unknown execution_mode: {execution_mode!r}")

    from app.services.message_service import render_template
    from app.utils.schema import validate_with_coercion
    import jsonschema as _js

    now = _now()
    batch = Batch(
        id=uuid_lib.uuid4(),
        user_id=uuid_lib.UUID(user_id),
        kind=BatchKind.AGENT,
        target_namespace=namespace,
        target_name=name,
        total=len(inputs),
        status=BatchStatus.QUEUED,
        trigger_id_prefix=trigger_id_prefix,
        callback_url=callback_url,
        batch_callback_url=batch_callback_url,
        started_at=now,
        created_at=now,
        execution_mode=execution_mode,
        llm_provider_id=provider_row.id if provider_row else None,
    )
    db.add(batch)
    await db.flush()

    execution_ids: list[str] = []
    chat_ids: list[str] = []
    provider_children: list[tuple[Chat, dict[str, Any]]] = []

    for i, item in enumerate(inputs):
        input_variables = item.get("input_variables") or {}
        message_content = item["message"]

        # Validate input_variables against the agent's input_schema if present.
        if agent.input_schema and input_variables:
            try:
                input_variables = validate_with_coercion(input_variables, agent.input_schema)
            except _js.ValidationError as e:
                raise BatchError(f"inputs[{i}].input_variables: {e.message}")

        chat = Chat(
            user_id=uuid_lib.UUID(user_id),
            agent_id=agent.id,
            agent_namespace=namespace,
            agent_name=name,
            title=f"batch:{batch.id}:{i}",
            chat_metadata={"agent_input": input_variables} if input_variables else None,
            job_timeout=agent.default_job_timeout,
        )
        db.add(chat)
        await db.flush()
        chat_ids.append(str(chat.id))

        # Initial messages (system / preamble), templated with input_variables.
        if agent.initial_messages:
            for msg_data in agent.initial_messages:
                content = msg_data["content"]
                if isinstance(content, str) and input_variables:
                    try:
                        content = render_template(content, input_variables)
                    except Exception:
                        pass
                db.add(Message(chat_id=chat.id, role=msg_data["role"], content=content))

        # Provider mode: persist the user message now (the queue worker does
        # this in queue mode) so history building and the final transcript
        # include it.
        if execution_mode == "provider":
            db.add(Message(chat_id=chat.id, role="user", content=message_content))
            provider_children.append((chat, input_variables))

        execution_id = str(uuid_lib.uuid4())
        execution_ids.append(execution_id)

        # Execution row tracks the async run. function_name holds the agent
        # ref for batch-children — kind=AGENT on the batch disambiguates.
        execution = Execution(
            user_id=uuid_lib.UUID(user_id),
            execution_id=execution_id,
            function_name=f"{namespace}/{name}",
            trigger_type=TriggerType.AGENT,
            trigger_id=_trigger_id(trigger_id_prefix, i),
            chat_id=chat.id,
            status=ExecutionStatus.PENDING,
            input_data={"input_variables": input_variables, "message": message_content},
            started_at=now,
            batch_id=batch.id,
        )
        db.add(execution)

    if execution_mode == "provider":
        # Build each child's request through the same pipeline as live chats
        # (system prompt rendering, preloaded skills, output-schema
        # instruction), then submit the whole batch to the provider. Rows are
        # only flushed so a submission failure rolls everything back.
        from app.services.conversation_history import build_conversation_history
        from app.services.skill_tools import SkillToolConverter

        await db.flush()
        skill_converter = SkillToolConverter()

        requests = []
        for execution_id, (chat, input_variables) in zip(execution_ids, provider_children):
            messages = await build_conversation_history(
                db=db,
                chat=chat,
                skill_converter=skill_converter,
                inject_context=False,
                user_id=user_id,
                template_variables=input_variables or None,
                provider_type=provider_row.provider_type,
            )
            requests.append({
                "custom_id": execution_id,
                "messages": messages,
                "model": batch_model,
                "temperature": agent.temperature,
                "max_tokens": agent.max_tokens,
            })

        try:
            provider_batch_id = await provider.submit_batch(requests)
        except BatchError:
            raise
        except Exception as e:
            await db.rollback()
            raise BatchError(f"Provider batch submission failed: {e}")

        batch.provider_batch_id = provider_batch_id
        batch.status = BatchStatus.RUNNING
        await db.commit()

        # Metering: queue-mode children are counted at the agent leaf in
        # message_service when each turn runs; provider-mode children never
        # pass through it, so count the whole batch at submission — this is
        # the moment the work is irrevocably bought from the provider.
        from app.services import metering

        await metering.record(metering.OperationKind.AGENT, n=len(requests))

        logger.info(
            "Submitted provider batch %s (%d requests) as %s via %s",
            batch.id, len(requests), provider_batch_id, provider_row.name,
        )
    else:
        await db.commit()

        # Enqueue agent message jobs.
        for execution_id, chat_id, item in zip(execution_ids, chat_ids, inputs):
            await queue_service.enqueue_agent_message(
                chat_id=chat_id,
                user_id=user_id,
                user_token=user_token,
                content=item["message"],
                channel_id=f"batch:{batch.id}:{execution_id}",
                agent=f"{namespace}/{name}",
                trigger_type=TriggerType.AGENT.value,
                execution_id=execution_id,
            )

    return {
        "batch_id": str(batch.id),
        "execution_ids": execution_ids,
        "chat_ids": chat_ids,
        "total": batch.total,
        "status": batch.status.value.lower(),
        "execution_mode": execution_mode,
    }


# ──────────────── Polling ────────────────


async def get_batch_status(
    *, db: AsyncSession, batch_id: str, user_id: str
) -> Optional[dict[str, Any]]:
    """Return aggregate status + per-status counts for a batch.

    Returns None if the batch doesn't exist or is owned by another user.
    """
    result = await db.execute(select(Batch).where(Batch.id == uuid_lib.UUID(batch_id)))
    batch = result.scalar_one_or_none()
    if not batch:
        return None
    if str(batch.user_id) != user_id:
        return None

    counts = await _count_by_status(db, batch.id)
    return {
        "batch_id": str(batch.id),
        "kind": batch.kind.value.lower(),
        "target": {"namespace": batch.target_namespace, "name": batch.target_name},
        "user_id": str(batch.user_id),
        "total": batch.total,
        "completed": counts.get(ExecutionStatus.COMPLETED.value, 0),
        "failed": counts.get(ExecutionStatus.FAILED.value, 0),
        "cancelled": counts.get(ExecutionStatus.CANCELLED.value, 0),
        "running": counts.get(ExecutionStatus.RUNNING.value, 0),
        "queued": counts.get(ExecutionStatus.PENDING.value, 0)
                  + counts.get(ExecutionStatus.AWAITING_INPUT.value, 0),
        "status": batch.status.value.lower(),
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "trigger_id_prefix": batch.trigger_id_prefix,
        "execution_mode": batch.execution_mode,
    }


async def _count_by_status(db: AsyncSession, batch_id: uuid_lib.UUID) -> dict[str, int]:
    rows = await db.execute(
        select(Execution.status, func.count(Execution.id))
        .where(Execution.batch_id == batch_id)
        .group_by(Execution.status)
    )
    return {row[0].value: row[1] for row in rows}


async def list_batch_executions(
    *,
    db: AsyncSession,
    batch_id: str,
    user_id: str,
    status: Optional[ExecutionStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> Optional[list[dict[str, Any]]]:
    result = await db.execute(select(Batch).where(Batch.id == uuid_lib.UUID(batch_id)))
    batch = result.scalar_one_or_none()
    if not batch:
        return None
    if str(batch.user_id) != user_id:
        return None

    stmt = select(Execution).where(Execution.batch_id == batch.id)
    if status is not None:
        stmt = stmt.where(Execution.status == status)
    stmt = stmt.order_by(Execution.started_at.asc()).limit(limit).offset(offset)

    rows = await db.execute(stmt)
    out = []
    for ex in rows.scalars().all():
        out.append({
            "execution_id": ex.execution_id,
            "status": ex.status.value.lower(),
            "function_name": ex.function_name,
            "chat_id": str(ex.chat_id) if ex.chat_id else None,
            "trigger_id": str(ex.trigger_id),
            "started_at": ex.started_at.isoformat() if ex.started_at else None,
            "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
            "duration_ms": ex.duration_ms,
            "error": ex.error,
        })
    return out


# ──────────────── Cancel ────────────────


async def cancel_batch(
    *, db: AsyncSession, batch_id: str, user_id: str
) -> Optional[dict[str, Any]]:
    result = await db.execute(select(Batch).where(Batch.id == uuid_lib.UUID(batch_id)))
    batch = result.scalar_one_or_none()
    if not batch:
        return None
    if str(batch.user_id) != user_id:
        return None
    if batch.finished_at is not None:
        # Already terminal — no-op.
        return {
            "batch_id": str(batch.id),
            "status": batch.status.value.lower(),
            "cancelled_children": 0,
        }

    # Provider-mode: best-effort cancel at the provider first. Children are
    # PENDING (there are no queue jobs), so the generic cancellation below
    # marks them all CANCELLED; any results the provider still returns are
    # ignored by the poller's already-terminal check.
    if batch.execution_mode == "provider" and batch.provider_batch_id:
        try:
            from app.models import LLMProvider
            from app.providers import create_provider

            row = await db.execute(
                select(LLMProvider).where(LLMProvider.id == batch.llm_provider_id)
            )
            provider_row = row.scalar_one_or_none()
            if provider_row:
                provider = await create_provider(provider_name=provider_row.name, db=db)
                await provider.cancel_batch(batch.provider_batch_id)
        except Exception as e:
            logger.warning(
                "Provider-side cancel failed for batch %s (%s): %s",
                batch.id, batch.provider_batch_id, e,
            )

    # Cancel queued / pending children. Running ones must complete naturally
    # (arq doesn't support clean preemption from outside the worker).
    cancellable = (ExecutionStatus.PENDING, ExecutionStatus.AWAITING_INPUT)
    upd = await db.execute(
        update(Execution)
        .where(Execution.batch_id == batch.id, Execution.status.in_(cancellable))
        .values(status=ExecutionStatus.CANCELLED, completed_at=_now())
    )
    cancelled_children = upd.rowcount or 0

    batch.status = BatchStatus.CANCELLED
    batch.finished_at = _now()
    await db.commit()

    # If everything was cancellable (no running children), fire the batch
    # callback now. Otherwise the last running child will fire it on
    # terminal-check.
    pending = await _count_pending(db, batch.id)
    if pending == 0 and batch.batch_callback_url:
        await _fire_batch_callback(db, batch)

    return {
        "batch_id": str(batch.id),
        "status": batch.status.value.lower(),
        "cancelled_children": cancelled_children,
    }


# ──────────────── Completion ────────────────


async def _count_pending(db: AsyncSession, batch_id: uuid_lib.UUID) -> int:
    """How many children haven't reached a terminal status yet."""
    result = await db.execute(
        select(func.count(Execution.id))
        .where(
            Execution.batch_id == batch_id,
            Execution.status.notin_(TERMINAL_EXEC_STATUSES),
        )
    )
    return int(result.scalar() or 0)


def _derive_terminal_status(counts: dict[str, int]) -> BatchStatus:
    """Compute the batch's terminal status from per-status counts."""
    completed = counts.get(ExecutionStatus.COMPLETED.value, 0)
    failed = counts.get(ExecutionStatus.FAILED.value, 0)
    cancelled = counts.get(ExecutionStatus.CANCELLED.value, 0)
    total_terminal = completed + failed + cancelled
    if total_terminal == 0:
        return BatchStatus.FAILED  # should not happen — but cover the edge
    if cancelled > 0 and completed == 0 and failed == 0:
        return BatchStatus.CANCELLED
    if completed == total_terminal:
        return BatchStatus.COMPLETED
    if failed == total_terminal:
        return BatchStatus.FAILED
    return BatchStatus.PARTIAL


async def on_execution_terminated(
    *, db: AsyncSession, batch_id: Optional[uuid_lib.UUID]
) -> None:
    """Called by the executor whenever a child execution hits a terminal
    status. Checks if the batch is now complete, and if so fires the batch
    callback exactly once (race-safe via CAS on finished_at)."""
    if batch_id is None:
        return

    pending = await _count_pending(db, batch_id)
    if pending > 0:
        # Bump batch.status to RUNNING the first time we see any non-queued state.
        await db.execute(
            update(Batch)
            .where(Batch.id == batch_id, Batch.status == BatchStatus.QUEUED)
            .values(status=BatchStatus.RUNNING)
        )
        await db.commit()
        return

    # All children terminal — try to claim the terminator slot.
    counts = await _count_by_status(db, batch_id)
    final_status = _derive_terminal_status(counts)

    cas = await db.execute(
        update(Batch)
        .where(Batch.id == batch_id, Batch.finished_at.is_(None))
        .values(status=final_status, finished_at=_now())
    )
    await db.commit()

    if (cas.rowcount or 0) == 0:
        return  # someone else terminated it

    # Fire the batch callback (best-effort, off the critical path).
    result = await db.execute(select(Batch).where(Batch.id == batch_id))
    batch = result.scalar_one_or_none()
    if batch and batch.batch_callback_url:
        await _fire_batch_callback(db, batch, counts)


async def _fire_batch_callback(
    db: AsyncSession,
    batch: Batch,
    counts: Optional[dict[str, int]] = None,
) -> None:
    """POST the batch summary to batch_callback_url. Fire-and-forget."""
    import httpx
    from app.core.auth import create_access_token
    from datetime import timedelta

    if counts is None:
        counts = await _count_by_status(db, batch.id)

    # Fetch the user's email for the token (best-effort).
    from app.models.user import User
    user_row = await db.execute(select(User).where(User.id == batch.user_id))
    user = user_row.scalar_one_or_none()
    user_email = user.email if user else "unknown@unknown.com"
    token = create_access_token(str(batch.user_id), user_email, expires_delta=timedelta(minutes=5))

    payload = {
        "batch_id": str(batch.id),
        "kind": batch.kind.value.lower(),
        "target": {"namespace": batch.target_namespace, "name": batch.target_name},
        "status": batch.status.value.lower(),
        "total": batch.total,
        "completed": counts.get(ExecutionStatus.COMPLETED.value, 0),
        "failed": counts.get(ExecutionStatus.FAILED.value, 0),
        "cancelled": counts.get(ExecutionStatus.CANCELLED.value, 0),
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "trigger_id_prefix": batch.trigger_id_prefix,
    }

    status_label = "failed"
    response_code: Optional[int] = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                batch.batch_callback_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload, default=str),
            )
        response_code = resp.status_code
        status_label = "sent" if 200 <= resp.status_code < 300 else "failed"
    except httpx.HTTPError as e:
        logger.warning("Batch callback POST failed for %s → %s: %s",
                       batch.id, batch.batch_callback_url, e)
    except Exception as e:
        logger.warning("Unexpected batch callback error for %s → %s: %s",
                       batch.id, batch.batch_callback_url, e)

    try:
        batch.callback_status = status_label
        batch.callback_response_code = response_code
        await db.commit()
    except Exception:
        logger.exception("Failed to persist batch callback status for %s", batch.id)
