"""Tool approval rules — permission modes for agent tool calls.

Layered on the existing requires_approval machinery, not new machinery:
per-agent first-match glob rules over tool names decide auto vs ask,
"always allow" session grants remember a user's approval for the rest of
the chat, and anything unmatched falls back to the intrinsic
requires_approval flag, then to the agent's default.

Resolution order for one tool call:
1. per-chat session grant (the user clicked "always allow" earlier) → auto
2. first matching agent rule → its action ("auto" may deliberately
   override a function's requires_approval — that is the "auto-allow
   reads" use case, and both knobs belong to the same agent author)
3. intrinsic requires_approval (function flag / system-tool metadata) → ask
4. the agent's default ("auto" when unset — today's behavior)

Config shape (agents.tool_approvals):
    {"default": "auto" | "ask",
     "rules": [{"match": "<glob over tool name>", "action": "auto" | "ask"}]}
"""
import logging
from fnmatch import fnmatch
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GRANT_PREFIX = "chat:tool-grant:"
# Session grants outlive the loop but not the account: a week covers any
# realistic conversation without becoming a permanent standing grant.
_GRANT_TTL = 7 * 24 * 3600

AUTO = "auto"
ASK = "ask"


def resolve_action(
    tool_approvals: Optional[dict[str, Any]],
    tool_name: str,
    intrinsic_ask: bool,
    session_grants: set[str],
) -> str:
    """Decide "auto" or "ask" for one tool call. Pure function — see the
    module docstring for the order."""
    if tool_name in session_grants:
        return AUTO

    if isinstance(tool_approvals, dict):
        rules = tool_approvals.get("rules")
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict):
                continue
            pattern = rule.get("match")
            action = rule.get("action")
            if not isinstance(pattern, str) or not pattern or action not in (AUTO, ASK):
                continue
            if fnmatch(tool_name, pattern):
                return action

    if intrinsic_ask:
        return ASK

    default = tool_approvals.get("default", AUTO) if isinstance(tool_approvals, dict) else AUTO
    return default if default in (AUTO, ASK) else AUTO


def validate_tool_approvals(value: Any) -> Optional[dict[str, Any]]:
    """Validate the config shape at save time so a bad rule can never be
    stored and fail only when a chat uses it. Returns the normalized dict
    (None stays None); raises ValueError with a precise message."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("tool_approvals must be an object with optional 'default' and 'rules'")
    unknown = set(value) - {"default", "rules"}
    if unknown:
        raise ValueError(f"tool_approvals: unknown keys {sorted(unknown)}")
    default = value.get("default", AUTO)
    if default not in (AUTO, ASK):
        raise ValueError(f"tool_approvals.default must be 'auto' or 'ask', got {default!r}")
    rules = value.get("rules", [])
    if rules is None:
        rules = []
    if not isinstance(rules, list):
        raise ValueError("tool_approvals.rules must be a list of {match, action}")
    normalized = []
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"tool_approvals.rules[{i}] must be an object with 'match' and 'action'")
        extra = set(rule) - {"match", "action"}
        if extra:
            raise ValueError(f"tool_approvals.rules[{i}]: unknown keys {sorted(extra)}")
        pattern, action = rule.get("match"), rule.get("action")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"tool_approvals.rules[{i}].match must be a non-empty glob string")
        if action not in (AUTO, ASK):
            raise ValueError(f"tool_approvals.rules[{i}].action must be 'auto' or 'ask'")
        normalized.append({"match": pattern, "action": action})
    return {"default": default, "rules": normalized}


async def get_session_grants(chat_id: str) -> set[str]:
    """Tool names the user has 'always allowed' for this chat."""
    from app.core.redis import get_redis

    try:
        redis = await get_redis()
        members = await redis.smembers(f"{_GRANT_PREFIX}{chat_id}")
        return {m if isinstance(m, str) else m.decode() for m in members or set()}
    except Exception as e:
        # Fail closed: no grants — worst case the user is asked again.
        logger.warning(f"Failed to load session tool grants for chat {chat_id}: {e}")
        return set()


async def add_session_grant(chat_id: str, tool_name: str) -> None:
    from app.core.redis import get_redis

    redis = await get_redis()
    key = f"{_GRANT_PREFIX}{chat_id}"
    # One MULTI: never a grant set without its TTL.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.sadd(key, tool_name)
        pipe.expire(key, _GRANT_TTL)
        await pipe.execute()


# ---------------------------------------------------------------------------
# One round, several asked calls
# ---------------------------------------------------------------------------

async def round_decision(db, approval) -> Optional[dict[str, Any]]:
    """What the round `approval` belongs to has decided, once every asked
    call in it is decided — None while any is still open.

    Every PendingToolApproval of a round stores the whole round, so resuming
    on the first decision would run the other asked calls unchecked (and a
    second decision would run the round again). The round resumes once,
    with the rejected calls named so they are answered, not executed.

    Returns {"approved_ids": [...], "rejected_ids": [...]}.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.pending_approval import PendingToolApproval

    rows = (
        await db.execute(
            select(PendingToolApproval).where(
                PendingToolApproval.chat_id == approval.chat_id,
                PendingToolApproval.message_id == approval.message_id,
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    approved_ids: list[str] = []
    rejected_ids: list[str] = []
    for row in rows:
        decided = row.approved
        if decided is None:
            if row.expires_at is not None and row.expires_at <= now:
                decided = False  # the sweep will record it; it can never be approved now
            else:
                return None
        (approved_ids if decided else rejected_ids).append(row.tool_call_id)
    return {"approved_ids": approved_ids, "rejected_ids": rejected_ids}


def round_job_id(approval) -> str:
    """The one job id a round's resume runs under, whoever queues it."""
    return f"approval-round-{approval.message_id}"


def round_channel_id(approval) -> str:
    """The one stream channel a round's resume publishes on: two requests
    that both complete the round (or the sweep) hand out the same channel,
    so whichever enqueue the queue kept, every caller can follow it."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sinas:approval-round:{approval.message_id}"))
