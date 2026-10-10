"""Conversation compaction: summarize-and-continue instead of the cliff.

Without this, a chat that outgrows max_history_messages silently loses its
oldest messages. With it, the dropped prefix is summarized once
(asynchronously — never on the user's critical path) and the summary is
injected as context whenever windowing applies, so long-running chats keep
their thread.

The summary lives in chat.chat_metadata["compaction"]:
    {"summary": str, "covered_count": int, "updated_at": iso8601}
covered_count is how many of the chat's earliest messages the summary
covers. Refreshes are incremental: prior summary + the messages between
covered_count and the current window start produce the next summary, so
each message is summarized at most once. A Redis NX guard keeps one
refresh per chat at a time; every failure path leaves the previous
behavior (plain windowing) intact.
"""
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)

_COMPACT_GUARD_PREFIX = "chat:compacting:"
_GUARD_TTL = 300

_SUMMARY_SYSTEM_PROMPT = (
    "You maintain a running summary of a conversation between a user and an "
    "AI assistant so the assistant can continue seamlessly after older "
    "messages are dropped. Merge the prior summary (if any) with the new "
    "messages into ONE updated summary. Preserve: stated user preferences "
    "and facts, decisions made, unresolved questions or tasks, names of "
    "files/artifacts created or modified, and any commitments the assistant "
    "made. Be concise; use short bullet points; do not invent details."
)


def get_compaction(chat) -> Optional[dict[str, Any]]:
    meta = chat.chat_metadata or {}
    compaction = meta.get("compaction")
    if isinstance(compaction, dict) and compaction.get("summary"):
        return compaction
    return None


def summary_message(compaction: dict[str, Any]) -> dict[str, Any]:
    """The system message injected in place of the summarized prefix."""
    return {
        "role": "system",
        "content": (
            "## Earlier conversation (summarized)\n"
            "Older messages were removed to fit the context window. "
            "Summary of what happened:\n\n" + compaction["summary"]
        ),
    }


def _render_messages(messages) -> str:
    """Render Message rows into plain text for the summarizer."""
    lines: list[str] = []
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            calls = ", ".join(
                f"{tc.get('function', {}).get('name', '?')}("
                f"{str(tc.get('function', {}).get('arguments', ''))[:200]})"
                for tc in msg.tool_calls
            )
            prefix = f"assistant called tools: {calls}"
            if msg.content:
                lines.append(f"assistant: {msg.content[:1000]}")
            lines.append(prefix)
        elif msg.role == "tool":
            content = (msg.content or "")[:500]
            lines.append(f"tool result ({msg.name or '?'}): {content}")
        else:
            lines.append(f"{msg.role}: {(msg.content or '')[:2000]}")
    return "\n".join(lines)


def maybe_schedule_compaction(chat_id: str) -> None:
    """Fire-and-forget a compaction refresh for this chat. Never raises."""
    if not settings.compaction_enabled:
        return
    try:
        asyncio.get_running_loop().create_task(_guarded_run(chat_id))
    except RuntimeError:
        # No running loop (sync/offline caller) — skip; the next turn retries.
        pass


async def _guarded_run(chat_id: str) -> None:
    from app.core.redis import get_redis

    try:
        redis = await get_redis()
        if not await redis.set(f"{_COMPACT_GUARD_PREFIX}{chat_id}", "1", nx=True, ex=_GUARD_TTL):
            return
    except Exception as e:
        logger.warning(f"Compaction guard unavailable for chat {chat_id}: {e}")
        return
    try:
        await run_compaction(chat_id)
    except Exception:
        logger.exception(f"Compaction failed for chat {chat_id}")
    finally:
        try:
            await redis.delete(f"{_COMPACT_GUARD_PREFIX}{chat_id}")
        except Exception:
            pass


async def run_compaction(chat_id: str, db=None) -> Optional[dict[str, Any]]:
    """Bring the chat's summary up to the current window start.

    Returns the stored compaction dict, or None when nothing to do.
    `db` is injectable for tests; production callers let it open its own
    session.
    """
    if db is not None:
        return await _run_compaction_with_session(chat_id, db)

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return await _run_compaction_with_session(chat_id, session)


async def _run_compaction_with_session(chat_id: str, db) -> Optional[dict[str, Any]]:
    from app.models.chat import Chat, Message

    chat = (
        await db.execute(select(Chat).where(Chat.id == chat_id))
    ).scalar_one_or_none()
    if not chat:
        return None

    all_messages = (
        await db.execute(
            select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
        )
    ).scalars().all()

    target = len(all_messages) - settings.max_history_messages
    if target <= 0:
        return get_compaction(chat)

    existing = get_compaction(chat)
    covered = existing["covered_count"] if existing else 0
    if covered >= target:
        return existing

    delta = all_messages[covered:target]
    prior_summary = existing["summary"] if existing else ""

    summary = await _summarize(db, chat, prior_summary, delta)
    if not summary:
        return existing

    meta = dict(chat.chat_metadata or {})
    compaction = {
        "summary": summary,
        "covered_count": target,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    meta["compaction"] = compaction
    chat.chat_metadata = meta
    await db.commit()
    logger.info(
        f"Compacted chat {chat_id}: summary now covers the first {target} messages"
    )
    return compaction


async def _summarize(db, chat, prior_summary: str, delta_messages) -> Optional[str]:
    """One summarization call via the platform's LLM provider."""
    from app.providers import create_provider

    parts = []
    if prior_summary:
        parts.append(f"## Prior summary\n{prior_summary}")
    parts.append(f"## New messages to fold in\n{_render_messages(delta_messages)}")
    user_content = "\n\n".join(parts)

    # Providers don't fall back on model=None — resolve the default
    # provider's default model explicitly.
    from app.models.llm_provider import LLMProvider

    provider_row = (
        await db.execute(
            select(LLMProvider).where(
                LLMProvider.is_default == True, LLMProvider.is_active == True  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    model = provider_row.default_model if provider_row else None
    if not model:
        logger.warning("Compaction skipped: no default LLM provider/model configured")
        return None

    agent_label = (
        f"{chat.agent_namespace}/{chat.agent_name}"
        if chat.agent_namespace and chat.agent_name
        else None
    )
    llm = await create_provider(
        None,
        None,
        db,
        usage_context={
            "user_id": str(chat.user_id),
            "chat_id": str(chat.id),
            "agent": agent_label,
            "source": "compaction",
        },
    )
    response = await llm.complete(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        model=model,
        tools=None,
        temperature=0.2,
        max_tokens=settings.compaction_summary_max_tokens,
    )
    content = (response or {}).get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None
