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

    if tool_approvals:
        for rule in tool_approvals.get("rules") or []:
            pattern = rule.get("match")
            action = rule.get("action")
            if not pattern or action not in (AUTO, ASK):
                continue
            if fnmatch(tool_name, pattern):
                return action

    if intrinsic_ask:
        return ASK

    default = (tool_approvals or {}).get("default", AUTO)
    return default if default in (AUTO, ASK) else AUTO


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
    await redis.sadd(key, tool_name)
    await redis.expire(key, _GRANT_TTL)
