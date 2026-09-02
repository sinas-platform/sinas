"""Agent-to-agent delegation support (issue #90).

Delegation depth is carried on the arq job (`depth` kwarg) and exposed to the
tool layer through a ContextVar set by the agent job handlers — the tool code
that enqueues a child runs deep inside the message loop and has no access to
the job's kwargs.

Depth 0 = user-initiated chat; every `call_agent_*` hop adds 1. Delegated
jobs route to the dedicated sub-agent queue (see `queue_service`) so parents
waiting on children can never starve them of worker slots, and chains are
bounded by `settings.agent_max_delegation_depth`.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Set by execute_agent_message_job / execute_agent_resume_job from job kwargs.
current_delegation_depth: ContextVar[int] = ContextVar(
    "current_delegation_depth", default=0
)

# The stream channel of the job currently processing this conversation.
# Needed to suspend: the resume job must publish to the same channel the
# user is subscribed to. None when not running inside an agent job (e.g.
# direct/synchronous API paths) — suspend mode falls back to blocking there.
current_channel_id: ContextVar[str | None] = ContextVar(
    "current_channel_id", default=None
)

# Job-level fields a suspension must carry into the resume job: the batch
# Execution row to terminate, the stream TTL, and — when this job is itself
# a delegated child — the parent checkpoint to report to when done.
current_job_meta: ContextVar[dict] = ContextVar("current_job_meta", default={})


def child_depth_or_error() -> tuple[int, str | None]:
    """Depth for a would-be child delegation, or an error when over the bound."""
    depth = current_delegation_depth.get() + 1
    limit = settings.agent_max_delegation_depth
    if limit and depth > limit:
        return depth, (
            f"Delegation depth limit reached ({limit}). This agent is already "
            f"{depth - 1} delegation hop(s) deep; it cannot call another agent. "
            "Answer with the information available, or raise "
            "AGENT_MAX_DELEGATION_DEPTH if deeper chains are intended."
        )
    return depth, None


def delegation_entry(delegation: dict[str, Any]) -> dict[str, Any]:
    """Pending-completion entry for one delegated call — the sub_agent
    completer's payload shape (see deferred_completions)."""
    from app.services import deferred_completions

    entry: dict[str, Any] = {
        "completer": deferred_completions.SUB_AGENT,
        "sub_chat_id": delegation["sub_chat_id"],
        "agent": delegation["agent"],
    }
    deadline = deferred_completions.deadline_from_now(
        settings.agent_delegate_suspend_timeout_seconds
    )
    if deadline:
        entry["expires_at"] = deadline
    return entry


async def enqueue_delegation_children(
    *,
    pending_completion_id: str,
    delegations: list[dict[str, Any]],
    user_id: str,
    user_token: str,
    delegation_depth: int,
) -> None:
    """Enqueue the child agent jobs for a suspended round's delegations.

    Must run AFTER the checkpoint row exists — a fast child could otherwise
    report completion against a checkpoint that isn't there yet.
    """
    from app.services.queue_service import queue_service

    for d in delegations:
        await queue_service.enqueue_agent_message(
            chat_id=d["sub_chat_id"],
            user_id=user_id,
            user_token=user_token,
            content=d["content"],
            channel_id=str(uuid.uuid4()),  # child's own stream channel
            agent=d["agent"],
            depth=delegation_depth + 1,
            pending_delegation_id=pending_completion_id,
            parent_tool_call_id=d["tool_call_id"],
        )


async def suspend_delegations(
    db,
    *,
    chat_id: str,
    user_id: str,
    user_token: str,
    channel_id: str,
    delegations: list[dict[str, Any]],
    conversation_context: dict[str, Any],
) -> str:
    """Checkpoint a round suspending ONLY on delegations, then enqueue the
    children. Thin wrapper over the generic deferred-completion service —
    kept for callers that predate the unification. Returns the row id.

    `delegations`: [{"tool_call_id", "sub_chat_id", "agent", "content"}].
    """
    from app.services import deferred_completions

    row = await deferred_completions.suspend_round(
        db,
        chat_id=chat_id,
        user_id=user_id,
        channel_id=channel_id,
        entries={d["tool_call_id"]: delegation_entry(d) for d in delegations},
        conversation_context=conversation_context,
    )
    await enqueue_delegation_children(
        pending_completion_id=str(row.id),
        delegations=delegations,
        user_id=user_id,
        user_token=user_token,
        delegation_depth=conversation_context.get("delegation_depth", 0),
    )
    return str(row.id)


async def on_child_complete(
    pending_delegation_id: str,
    tool_call_id: str,
    content: str,
    *,
    user_token: str,
) -> None:
    """Record a finished child; when it is the last completion, wake the
    parent. Thin wrapper over the generic deferred-completion service (the
    checkpoint may also carry other completer kinds — e.g. an ask_user
    question suspended in the same round)."""
    from app.services import deferred_completions

    await deferred_completions.complete(
        pending_delegation_id,
        tool_call_id,
        content,
        user_token=user_token,
    )

