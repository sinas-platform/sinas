"""Chat steering: per-chat execution lock, cooperative interrupt, injection.

Long autonomous turns are only safe if exactly one agent loop runs per chat
(the lock), a human can stop a runaway loop without deleting the chat (the
interrupt — issue #142), and a message sent mid-turn steers the running
loop instead of racing it (injection).

Mechanics:

- Lock: a Redis SET NX token per chat, held for the duration of one agent
  loop (send, stream, or a resume/continuation job). A heartbeat keeps it
  alive for the whole loop, even through one long tool call; its TTL — the
  running job's timeout plus slack — is what frees the chat after a
  crashed worker, so a chat can never be wedged forever.
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
import asyncio
import logging
import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "chat:loop-lock:"
_INTERRUPT_PREFIX = "chat:interrupt:"
_INTERRUPT_AT_PREFIX = "chat:interrupt-at:"
_INJECTED_PREFIX = "chat:injected:"

# The token of the lock the current task holds, so the loop can heartbeat
# it at round boundaries without threading the token through every call.
current_lock_token: ContextVar[Optional[str]] = ContextVar("current_lock_token", default=None)

# How long an interrupt request stays armed. Long enough to catch queued
# continuation jobs that haven't started yet, short enough that a forgotten
# interrupt doesn't ambush next week's conversation.
INTERRUPT_TTL = 600


# The timeout the agent job running in this task is held to (set by
# agent_jobs._within_job_timeout). The lock's TTL is sized from it, so a
# chat whose job_timeout is longer than AGENT_JOB_TIMEOUT keeps its lock
# for as long as its turn may run.
current_job_timeout: ContextVar[Optional[float]] = ContextVar("current_job_timeout", default=None)
# When that job times out (event loop time): an LLM call's retry
# (app/services/llm_retry.py) never waits past it.
current_job_deadline: ContextVar[Optional[float]] = ContextVar("current_job_deadline", default=None)


@contextmanager
def job_deadline(seconds: float) -> Iterator[None]:
    """Mark that the work in this context (an agent job, or a synchronous
    request that runs a turn under its own timeout) ends in `seconds`, for
    current_job_deadline. Tasks started inside it inherit the deadline."""
    token = current_job_deadline.set(asyncio.get_running_loop().time() + seconds)
    try:
        yield
    finally:
        current_job_deadline.reset(token)

# Slack on top of the job timeout before an abandoned lock (a crashed
# worker) frees the chat.
LOCK_TTL_MARGIN = 120

# Held locks' heartbeat tasks, by token (see _heartbeat).
_heartbeats: dict[str, asyncio.Task] = {}

_EXTEND_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)


def _lock_ttl() -> int:
    timeout = current_job_timeout.get() or settings.agent_job_timeout
    return math.ceil(timeout) + LOCK_TTL_MARGIN


def _heartbeat_interval(ttl: float) -> float:
    return max(1.0, ttl / 3)


async def _heartbeat(chat_id: str, token: str, ttl: int) -> None:
    """Keep a held lock alive for the whole turn, not just between tool
    rounds: one tool call (a long code run, a slow MCP call) can outlast
    the TTL, and another turn must not start on the chat meanwhile.
    Extends only while the lock is still ours (token-checked); once it
    isn't (expired and taken over), stops and never takes it back.
    Cancelled by release_chat_lock, which every holder calls in a
    `finally`, so it ends with the turn, however the turn ends."""
    from app.core.redis import get_redis

    try:
        while True:
            await asyncio.sleep(_heartbeat_interval(ttl))
            try:
                redis = await get_redis()
                held = await redis.eval(_EXTEND_SCRIPT, 1, f"{_LOCK_PREFIX}{chat_id}", token, ttl)
            except Exception as e:  # transient: the TTL covers a few misses
                logger.warning("Chat lock heartbeat for %s failed: %s", chat_id, e)
                continue
            if not held:
                logger.warning("Chat lock for %s is no longer ours; heartbeat stopped", chat_id)
                return
    finally:
        if _heartbeats.get(token) is asyncio.current_task():
            del _heartbeats[token]


async def acquire_chat_lock(chat_id: str) -> Optional[str]:
    """Try to become the chat's one running loop. Returns a release token,
    or None when another loop already holds the chat. A held lock is kept
    alive by a heartbeat until release_chat_lock."""
    from app.core.redis import get_redis

    redis = await get_redis()
    token = uuid.uuid4().hex
    ttl = _lock_ttl()
    ok = await redis.set(f"{_LOCK_PREFIX}{chat_id}", token, nx=True, ex=ttl)
    if ok:
        current_lock_token.set(token)
        _heartbeats[token] = asyncio.create_task(_heartbeat(chat_id, token, ttl))
        return token
    return None


async def refresh_chat_lock(chat_id: str, token: Optional[str] = None) -> bool:
    """Extend the lock's TTL if we still hold it (token-checked, atomic).
    Called at every tool-round boundary on top of the heartbeat. Returns
    False when the lock is no longer ours (expired and taken over)."""
    from app.core.redis import get_redis

    token = token or current_lock_token.get()
    if not token:
        return False
    redis = await get_redis()
    try:
        return bool(await redis.eval(_EXTEND_SCRIPT, 1, f"{_LOCK_PREFIX}{chat_id}", token, _lock_ttl()))
    except Exception as e:
        logger.warning(f"Failed to refresh chat lock for {chat_id}: {e}")
        return False


async def release_chat_lock(chat_id: str, token: str) -> None:
    """Release the lock if we still hold it (token-checked, atomic), and
    stop its heartbeat."""
    from app.core.redis import get_redis

    heartbeat = _heartbeats.pop(token, None)
    if heartbeat is not None:
        heartbeat.cancel()
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
    if current_lock_token.get() == token:
        current_lock_token.set(None)


async def is_chat_locked(chat_id: str) -> bool:
    from app.core.redis import get_redis

    redis = await get_redis()
    return bool(await redis.exists(f"{_LOCK_PREFIX}{chat_id}"))


async def acquire_chat_lock_wait(chat_id: str, wait_seconds: float = 60) -> Optional[str]:
    """Acquire the lock, waiting up to `wait_seconds` for the current holder
    to finish. For continuation jobs (approval resume, delegate resume) that
    must run but should not race a still-active loop."""
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
    # When it was asked for: a continuation queued BEFORE this moment must
    # not run on, even if the running turn consumed the flag first.
    await redis.set(f"{_INTERRUPT_AT_PREFIX}{chat_id}", str(time.time()), ex=INTERRUPT_TTL)


async def interrupt_requested_at(chat_id: str) -> Optional[float]:
    """Unix time of the most recent interrupt request still in force, if any."""
    from app.core.redis import get_redis

    redis = await get_redis()
    raw = await redis.get(f"{_INTERRUPT_AT_PREFIX}{chat_id}")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


async def consume_interrupt(chat_id: str) -> bool:
    """Check-and-clear the interrupt flag (the boundary check)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    return bool(await redis.getdel(f"{_INTERRUPT_PREFIX}{chat_id}"))


async def clear_interrupt(chat_id: str) -> None:
    """Disarm a pending interrupt (a fresh user turn starts clean)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    await redis.delete(f"{_INTERRUPT_PREFIX}{chat_id}", f"{_INTERRUPT_AT_PREFIX}{chat_id}")


async def note_injection(chat_id: str) -> None:
    """A message was injected into a running loop. Counted so the loop can
    tell, when its turn ends, whether anything arrived that no round
    boundary answered (a turn that ends in plain text has no boundary)."""
    from app.core.redis import get_redis

    redis = await get_redis()
    # One MULTI: never a counter without its TTL.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(f"{_INJECTED_PREFIX}{chat_id}")
        pipe.expire(f"{_INJECTED_PREFIX}{chat_id}", INTERRUPT_TTL * 6)
        await pipe.execute()


async def take_injections(chat_id: str) -> int:
    """Number of injections since the last take; resets the count."""
    from app.core.redis import get_redis

    redis = await get_redis()
    raw = await redis.getdel(f"{_INJECTED_PREFIX}{chat_id}")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


INTERRUPT_MARKER = "Interrupted by operator."
