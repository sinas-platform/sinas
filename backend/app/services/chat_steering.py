"""Chat steering: per-chat execution lock, cooperative interrupt, injection.

Long autonomous turns are only safe if exactly one agent loop runs per chat
(the lock), a human can stop a runaway loop without deleting the chat (the
interrupt — issue #142), and a message sent mid-turn steers the running
loop instead of racing it (injection).

Mechanics:

- Lock: a Redis SET NX token per chat, held for the duration of one agent
  loop (send, stream, or a resume/continuation job). The TTL is generous —
  the agent job timeout plus slack — so a crashed worker can never wedge a
  chat forever.
- Interrupt: a Redis flag checked at tool-round boundaries. The loop
  consumes it, writes an "interrupted by operator" system marker into the
  transcript, and stops. The flag has its own TTL so an interrupt aimed at
  a loop that already ended also catches the queued continuation jobs the
  issue asks about — and a fresh user message clears it, so a stale flag
  never kills a new turn.
- Injection needs no machinery here: the loop rebuilds its conversation
  from the DB at every round boundary, so a user message row persisted
  while the lock is held is picked up naturally. MessageService uses the
  lock to decide send-vs-inject.
"""
import logging
import uuid
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "chat:loop-lock:"
_INTERRUPT_PREFIX = "chat:interrupt:"

# How long an interrupt request stays armed. Long enough to catch queued
# continuation jobs that haven't started yet, short enough that a forgotten
# interrupt doesn't ambush next week's conversation.
INTERRUPT_TTL = 600


def _lock_ttl() -> int:
    return settings.agent_job_timeout + 120


async def acquire_chat_lock(chat_id: str) -> Optional[str]:
    """Try to become the chat's one running loop. Returns a release token,
    or None when another loop already holds the chat."""
    from app.core.redis import get_redis

    redis = await get_redis()
    token = uuid.uuid4().hex
    ok = await redis.set(f"{_LOCK_PREFIX}{chat_id}", token, nx=True, ex=_lock_ttl())
    return token if ok else None


async def release_chat_lock(chat_id: str, token: str) -> None:
    """Release the lock if we still hold it (token-checked, atomic)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    # Compare-and-delete so an expired-and-reacquired lock is never released
    # by the previous holder.
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
        await redis.eval(script, 1, f"{_LOCK_PREFIX}{chat_id}", token)
    except Exception as e:
        logger.warning(f"Failed to release chat lock for {chat_id}: {e}")


async def is_chat_locked(chat_id: str) -> bool:
    from app.core.redis import get_redis

    redis = await get_redis()
    return bool(await redis.exists(f"{_LOCK_PREFIX}{chat_id}"))


async def acquire_chat_lock_wait(chat_id: str, wait_seconds: float = 60) -> Optional[str]:
    """Acquire the lock, waiting up to `wait_seconds` for the current holder
    to finish. For continuation jobs (approval resume, delegate resume) that
    must run but should not race a still-active loop."""
    import asyncio
    import time

    deadline = time.monotonic() + wait_seconds
    while True:
        token = await acquire_chat_lock(chat_id)
        if token or time.monotonic() >= deadline:
            return token
        await asyncio.sleep(0.5)


async def request_interrupt(chat_id: str, requested_by: str = "") -> None:
    """Arm the interrupt flag; the running loop stops at its next boundary,
    and continuation jobs that start within the TTL stop immediately."""
    from app.core.redis import get_redis

    redis = await get_redis()
    await redis.set(f"{_INTERRUPT_PREFIX}{chat_id}", requested_by or "1", ex=INTERRUPT_TTL)


async def consume_interrupt(chat_id: str) -> bool:
    """Check-and-clear the interrupt flag (the boundary check)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    return bool(await redis.getdel(f"{_INTERRUPT_PREFIX}{chat_id}"))


async def clear_interrupt(chat_id: str) -> None:
    """Disarm a pending interrupt (a fresh user turn starts clean)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    await redis.delete(f"{_INTERRUPT_PREFIX}{chat_id}")


INTERRUPT_MARKER = "Interrupted by operator."
