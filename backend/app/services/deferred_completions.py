"""Deferred tool rounds — suspend a tool round on completions that arrive later.

The unifying abstraction behind three previously bespoke pause/resume
mechanisms: a tool round can suspend with one or more *pending completions*,
each owned by a pluggable *completer*, and resumes when the last completion
lands (or its deadline passes). One `PendingCompletion` row checkpoints the
whole round.

Completers own three things about an entry: who supplies the result
(a finishing sub-agent job, a human answering over the API, the expiry
sweep), what tool-message `name` the result is recorded under, and what the
result content is when the entry times out. The core is completer-agnostic:
`complete()` persists the tool result, decrements the outstanding count,
and — on the last completion — enqueues the round-resume job
(`execute_agent_delegate_resume_job`, which re-enters
`MessageService._stream_followup_after_tools` under the chat lock).

Kinds today:
- ``sub_agent``  — suspend-on-delegate (issue #90); completed by the child
  agent job's terminal handler via `delegation.on_child_complete`.
- ``human_input`` — the `ask_user` system tool; completed by
  `POST /chats/{id}/pending-input/{tool_call_id}`.

Invariants the resume path must keep (verified by tests):
- every tool_call in the suspended assistant message ends up with a tool
  result row before the round resumes — completions and timeouts both write
  the result *first*, in the same transaction that decrements the count;
- the resume job holds the per-chat lock and checks the cooperative
  interrupt (it re-enters the same follow-up path as an inline round).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Stream event type announcing any suspension (superset of the older
# delegation_pending event, which is still emitted for delegation rounds).
ROUND_SUSPENDED = "round_suspended"
# Event types after which the enclosing agent job must NOT publish "done" /
# terminate — a resume job owns the rest of the conversation.
SUSPENSION_EVENT_TYPES = frozenset({ROUND_SUSPENDED, "delegation_pending"})

SUB_AGENT = "sub_agent"
HUMAN_INPUT = "human_input"


@dataclass(frozen=True)
class Completer:
    """How one kind of deferred completion behaves.

    `tool_message_name` names the tool-result Message row (providers echo it
    back); `timeout_content` is the tool result written when the entry's
    deadline passes — the round then resumes with that error, it never hangs.
    """

    kind: str
    tool_message_name: Callable[[dict[str, Any]], Optional[str]]
    timeout_content: Callable[[str, dict[str, Any]], str]


def _sub_agent_name(entry: dict[str, Any]) -> Optional[str]:
    agent = entry.get("agent")
    return f"call_agent_{agent.replace('/', '__')}" if agent else None


_COMPLETERS: dict[str, Completer] = {}


def register_completer(completer: Completer) -> None:
    _COMPLETERS[completer.kind] = completer


def get_completer(kind: str) -> Completer:
    """Look up a completer; unknown kinds degrade to a generic one so a
    checkpoint written by newer code never wedges an older worker."""
    return _COMPLETERS.get(kind) or Completer(
        kind=kind,
        tool_message_name=lambda entry: None,
        timeout_content=lambda tc_id, entry: json.dumps(
            {"error": "Deferred tool call timed out before a result arrived"}
        ),
    )


register_completer(
    Completer(
        kind=SUB_AGENT,
        tool_message_name=_sub_agent_name,
        timeout_content=lambda tc_id, entry: json.dumps(
            {
                "error": "Sub-agent did not report back before the deadline",
                "chat_id": entry.get("sub_chat_id"),
            }
        ),
    )
)

register_completer(
    Completer(
        kind=HUMAN_INPUT,
        tool_message_name=lambda entry: "ask_user",
        timeout_content=lambda tc_id, entry: json.dumps(
            {
                "error": (
                    "The user did not answer within the time limit. Continue "
                    "with the information you have, state the assumption you "
                    "are making, or finish and let the user follow up."
                ),
                "timed_out": True,
            }
        ),
    )
)


def entry_kind(entry: dict[str, Any]) -> str:
    """Completer kind of a pending entry. Checkpoint rows written before the
    unification carry no "completer" key — they were all delegations."""
    return entry.get("completer") or SUB_AGENT


def _min_entry_expiry(pending: dict[str, Any]) -> Optional[datetime]:
    deadlines = []
    for entry in (pending or {}).values():
        raw = entry.get("expires_at")
        if raw:
            try:
                deadlines.append(datetime.fromisoformat(raw))
            except ValueError:
                logger.warning("Unparseable entry expires_at %r — ignoring", raw)
    return min(deadlines) if deadlines else None


def deadline_from_now(timeout_seconds: int) -> Optional[str]:
    """ISO deadline `timeout_seconds` from now; None disables expiry (<= 0)."""
    if not timeout_seconds or timeout_seconds <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()


async def suspend_round(
    db,
    *,
    chat_id: str,
    user_id: str,
    channel_id: str,
    entries: dict[str, dict[str, Any]],
    conversation_context: dict[str, Any],
):
    """Persist a checkpoint for a suspending tool round.

    `entries`: {tool_call_id: {"completer": kind, ...payload,
    "expires_at": iso-datetime?}}. The row must exist before any completer
    is started (e.g. before child jobs are enqueued) — a fast completion
    must never race a checkpoint that isn't there yet. Returns the row.
    """
    from app.models.pending_completion import PendingCompletion

    row = PendingCompletion(
        chat_id=chat_id,
        user_id=user_id,
        channel_id=channel_id,
        pending=entries,
        results={},
        remaining=len(entries),
        conversation_context=conversation_context,
        expires_at=_min_entry_expiry(entries),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "Suspended chat %s on %d pending completion(s) [%s] (pending_completion=%s)",
        chat_id,
        len(entries),
        ", ".join(sorted({entry_kind(e) for e in entries.values()})),
        row.id,
    )
    return row


async def complete(
    pending_completion_id: str,
    tool_call_id: str,
    content: str,
    *,
    user_token: str,
    resume_channel_id: Optional[str] = None,
) -> dict[str, Any]:
    """Record one finished completion; when it is the last, resume the round.

    Writes the result as the parent's tool-role Message row (the source the
    follow-up LLM turn is rebuilt from) in the same transaction that
    decrements the outstanding count, then — on the last completion —
    deletes the checkpoint and enqueues the resume job. Concurrency-safe via
    SELECT ... FOR UPDATE on the checkpoint row; a completion for an entry
    that is no longer pending (double delivery, already expired) is a no-op.

    `resume_channel_id`: publish the resumed conversation to this fresh
    channel instead of the suspended round's original one — for completions
    arriving over the API long after the original stream closed (mirrors the
    approval flow's reconnect contract).

    Returns {"status": "completed" | "not_found" | "unknown_tool_call",
    "resumed": bool, "channel_id": the resume channel when resumed}.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.chat import Message
    from app.models.pending_completion import PendingCompletion
    from app.services.queue_service import queue_service

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PendingCompletion)
            .where(PendingCompletion.id == pending_completion_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if not row:
            logger.warning(
                "PendingCompletion %s not found for tool_call %s — round already resumed or cleaned up",
                pending_completion_id,
                tool_call_id,
            )
            return {"status": "not_found", "resumed": False}

        entry = (row.pending or {}).get(tool_call_id)
        if entry is None:
            logger.warning(
                "tool_call %s is not pending on completion %s — duplicate delivery or already expired",
                tool_call_id,
                pending_completion_id,
            )
            return {"status": "unknown_tool_call", "resumed": False}

        completer = get_completer(entry_kind(entry))
        tool_name = completer.tool_message_name(entry)

        # Persist the tool result on the parent conversation NOW, in this
        # transaction — the resume job rebuilds messages from the DB, and an
        # assistant tool_calls message must never be left without results.
        db.add(
            Message(
                chat_id=row.chat_id,
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )

        # JSON columns need reassignment (in-place mutation isn't tracked).
        results = dict(row.results or {})
        results[tool_call_id] = content
        row.results = results
        pending = dict(row.pending or {})
        pending.pop(tool_call_id, None)
        row.pending = pending
        row.remaining = row.remaining - 1
        row.expires_at = _min_entry_expiry(pending)

        is_last = row.remaining <= 0
        ctx = row.conversation_context
        chat_id, user_id, channel_id = str(row.chat_id), str(row.user_id), row.channel_id
        if is_last:
            await db.delete(row)
        await db.commit()

    # Progressive UX: close this tool call on the suspended round's stream
    # now, even though the conversation only continues on the last landing.
    try:
        from app.services.stream_relay import stream_relay

        await stream_relay.publish(
            channel_id,
            {
                "type": "tool_end",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "result": content,
            },
        )
    except Exception:
        logger.debug("Could not publish deferred tool_end to %s", channel_id)

    if not is_last:
        return {"status": "completed", "resumed": False}

    resume_channel = resume_channel_id or channel_id
    await queue_service.enqueue_agent_delegate_resume(
        chat_id=chat_id,
        user_id=user_id,
        user_token=user_token,
        channel_id=resume_channel,
        conversation_context=ctx,
    )
    logger.info(
        "All pending completions landed for chat %s — resume job enqueued", chat_id
    )
    return {"status": "completed", "resumed": True, "channel_id": resume_channel}


async def list_pending_inputs(db, chat_id: str) -> list[dict[str, Any]]:
    """Open ask_user questions for a chat, oldest first.

    [{pending_completion_id, tool_call_id, question, options, expires_at,
    created_at}] — the queryable surface behind GET pending-input and the
    chat detail response.
    """
    from app.models.pending_completion import PendingCompletion

    result = await db.execute(
        select(PendingCompletion)
        .where(PendingCompletion.chat_id == chat_id)
        .order_by(PendingCompletion.created_at)
    )
    out: list[dict[str, Any]] = []
    for row in result.scalars().all():
        for tool_call_id, entry in (row.pending or {}).items():
            if entry_kind(entry) != HUMAN_INPUT:
                continue
            out.append(
                {
                    "pending_completion_id": str(row.id),
                    "tool_call_id": tool_call_id,
                    "question": entry.get("question", ""),
                    "options": entry.get("options"),
                    "expires_at": entry.get("expires_at"),
                    "created_at": row.created_at,
                }
            )
    return out


async def expire_due(now: Optional[datetime] = None) -> int:
    """Resolve every pending thing whose deadline has passed. Returns count.

    - Pending completions: each overdue entry gets its completer's timeout
      content as the tool result, through the same `complete()` path — so
      the last timeout resumes the round like any other completion. The
      resume runs without a user token; follow-up tool calls that need one
      will fail with auth errors the model can report.
    - Pending tool approvals: still-unanswered rows past their deadline are
      auto-rejected (approved=False), same terminal state as a user
      rejection; the transcript repair in conversation_history covers the
      never-executed calls on the next turn.

    Runs from the worker cron sweep. Safe to run concurrently: `complete()`
    locks per row, and an entry that lands in between is skipped.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.pending_approval import PendingToolApproval
    from app.models.pending_completion import PendingCompletion

    now = now or datetime.now(timezone.utc)
    resolved = 0

    overdue: list[tuple[str, str, dict[str, Any]]] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PendingCompletion).where(PendingCompletion.expires_at <= now)
        )
        for row in result.scalars().all():
            for tool_call_id, entry in (row.pending or {}).items():
                raw = entry.get("expires_at")
                if not raw:
                    continue
                try:
                    deadline = datetime.fromisoformat(raw)
                except ValueError:
                    continue
                if deadline <= now:
                    overdue.append((str(row.id), tool_call_id, dict(entry)))

    for row_id, tool_call_id, entry in overdue:
        completer = get_completer(entry_kind(entry))
        outcome = await complete(
            row_id,
            tool_call_id,
            completer.timeout_content(tool_call_id, entry),
            user_token="",
        )
        if outcome["status"] == "completed":
            resolved += 1
            logger.info(
                "Expired pending completion entry %s (%s) on %s",
                tool_call_id,
                entry_kind(entry),
                row_id,
            )

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PendingToolApproval)
            .where(
                PendingToolApproval.approved.is_(None),
                PendingToolApproval.expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
        for approval in result.scalars().all():
            approval.approved = False
            resolved += 1
            logger.info(
                "Expired pending approval %s (tool_call %s) — auto-rejected",
                approval.id,
                approval.tool_call_id,
            )
        await db.commit()

    return resolved
