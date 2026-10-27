"""Background work and wakeups: let an agent start something, go idle, and be
woken when it finishes or when something happens (ADR
2026-10-11-background-jobs-and-wakeups).

Three ways in, one way out:

- `run_in_background(code)` queues a sandbox run and returns at once; when
  it finishes, the result is delivered into the chat. With `wait: true` the
  tool round suspends instead (a `background_job` pending completion) and
  resumes with the result — no worker slot held meanwhile.
- `schedule_wakeup(delay_seconds, note)` creates a timer; a minutely sweep
  delivers the note into the chat when it is due.
- `create_watch(description)` creates a callback URL; each POST to it is
  delivered into the chat.

"Delivered into the chat" (`deliver_to_chat`) means: if a turn is running or
the chat is suspended on a pending completion, the message is injected and
picked up at the next round boundary; otherwise a new turn starts on a
fresh stream channel, acting through the API key the chat's turn acted
through. Every delivery is a user-role message with a bracketed prefix the
way-of-working skill teaches the agent to recognise.
"""
import asyncio
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select, update

from app.core.config import settings
from app.models.chat import Chat
from app.models.chat_wakeup import TIMER, WATCH, ChatWakeup
from app.models.user import User

logger = logging.getLogger(__name__)

BACKGROUND_SYSTEM_TOOL = "background"
RUN_IN_BACKGROUND = "run_in_background"
BACKGROUND_JOB_STATUS = "background_job_status"
SCHEDULE_WAKEUP = "schedule_wakeup"
CANCEL_WAKEUP = "cancel_wakeup"
CREATE_WATCH = "create_watch"
DELETE_WATCH = "delete_watch"
LIST_WAKEUPS = "list_wakeups"

BACKGROUND_TOOL_NAMES = frozenset({
    RUN_IN_BACKGROUND, BACKGROUND_JOB_STATUS, SCHEDULE_WAKEUP, CANCEL_WAKEUP,
    CREATE_WATCH, DELETE_WATCH, LIST_WAKEUPS,
})

BACKGROUND_QUEUE = "sinas:queue:functions"
BG_JOB_PREFIX = "sinas:bgjob:"
BG_JOB_TTL = 86400
CHAT_BG_JOBS_PREFIX = "sinas:chat:bgjobs:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

async def parked_round_channel(db, chat_id) -> Optional[str]:
    """The stream channel of the chat's parked round, or None when no round
    is parked. A round is parked on a pending completion (an ask_user
    question, a suspended delegation, a background run being waited on) or
    an unanswered tool approval; it resumes on this channel.

    A parked round holds no chat lock, but it has open tool calls: a new
    turn would "repair" them with synthetic errors and race the round's
    resume, so a message for such a chat must be injected (read when the
    round resumes), never start a turn. A checkpoint whose last completion
    landed (remaining == 0) still parks the chat: its row stays until the
    saved turn is queued, and a turn started in that gap would run beside
    the saved one."""
    from app.models.pending_approval import PendingToolApproval
    from app.models.pending_completion import PendingCompletion
    from app.services.approval_rules import round_channel_id

    chat_uuid = chat_id if isinstance(chat_id, uuid.UUID) else uuid.UUID(str(chat_id))
    channel = (
        await db.execute(
            select(PendingCompletion.channel_id)
            .where(PendingCompletion.chat_id == chat_uuid)
            .order_by(PendingCompletion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if channel is not None:
        return channel
    approval = (
        await db.execute(
            select(PendingToolApproval)
            .where(
                PendingToolApproval.chat_id == chat_uuid,
                PendingToolApproval.approved.is_(None),
                or_(PendingToolApproval.expires_at.is_(None), PendingToolApproval.expires_at > _now()),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return round_channel_id(approval) if approval is not None else None


async def chat_is_parked(db, chat_id) -> bool:
    """Whether the chat has a round parked (see parked_round_channel)."""
    return await parked_round_channel(db, chat_id) is not None


async def deliver_to_chat(
    chat_id: str, content: str, *, source: str, api_key_id: Optional[str] = None, guard=None,
    delivery_id: Optional[str] = None,
) -> dict[str, Any]:
    """Put `content` in front of the agent of `chat_id`.

    Injects when a turn is running (chat lock held) or the chat is parked on
    a pending completion — a new turn would repair the suspended round's
    open tool calls with synthetic errors and corrupt it — and the running
    loop drains it at its next round boundary. Otherwise starts a new turn
    through the queue, on a fresh channel recorded as the chat's
    `active_channel_id` so a client can reconnect.

    `guard(db)`, when given, runs in the transaction that writes the
    delivery and commits with it; returning False withdraws the delivery
    ("dropped", reason "withdrawn"). A sender with an outbox uses it to take
    the message off in the same transaction as delivering it, so the
    message is delivered exactly when it leaves the outbox:

    - injected: the message row is written in that transaction;
    - a new turn: the turn is queued inside that transaction, before it
      commits (a Redis call, which cannot wait on the database, so holding
      the guard's locks across it can't deadlock). A failed enqueue rolls
      the guard back. `delivery_id` fixes the job id, so a crash after the
      enqueue and before the commit is retried without queuing the turn
      twice.

    Returns {"status": "injected" | "started" | "dropped", ...}.
    """
    from app.core.auth import bind_api_key, create_access_token, reset_api_key
    from app.core.database import AsyncSessionLocal
    from app.services import chat_steering
    from app.services.message_service import MessageService
    from app.services.queue_service import queue_service

    async with AsyncSessionLocal() as db:
        chat = (await db.execute(select(Chat).where(Chat.id == uuid.UUID(chat_id)))).scalar_one_or_none()
        if chat is None or chat.archived:
            logger.info("Wakeup for chat %s dropped: chat gone or archived", chat_id)
            return {"status": "dropped", "reason": "chat_unavailable"}
        user = (await db.execute(select(User).where(User.id == chat.user_id))).scalar_one_or_none()
        if user is None or not user.is_active:
            return {"status": "dropped", "reason": "owner_inactive"}

        suspended = await chat_is_parked(db, chat.id)

        fixed = uuid.uuid5(uuid.NAMESPACE_URL, f"sinas:delivery:{delivery_id}") if delivery_id else None
        fixed_job_id = f"delivery-{fixed}" if fixed else None
        earlier = await _job_state(fixed_job_id) if guard is not None and fixed_job_id else None
        if earlier is not None:
            # An earlier attempt queued this delivery's turn and died before
            # committing: the message is on its way in that turn already
            # (which may be the one holding the chat now). Only take it off.
            # While that turn is still queued or running, record its stream
            # channel (it rolled back with the failed commit) so a client can
            # reconnect; once it has finished, leave the channel alone: a
            # newer turn may own it.
            if not await guard(db):
                await db.rollback()
                return {"status": "dropped", "reason": "withdrawn"}
            if earlier in ("queued", "running"):
                chat.active_channel_id = str(fixed)
            await db.commit()
            return {"status": "started", "job_id": fixed_job_id, "channel_id": str(fixed), "already_queued": True}

        if suspended or await chat_steering.is_chat_locked(chat_id):
            withdrawn = False

            async def checked(session) -> bool:
                nonlocal withdrawn
                withdrawn = not await guard(session)
                return not withdrawn

            injected = await MessageService(db).inject_user_message(
                chat_id, str(chat.user_id), content, guard=checked if guard is not None else None
            )
            if injected is None:
                return {"status": "dropped", "reason": "withdrawn" if withdrawn else "blocked_by_hook"}
            logger.info("Wakeup (%s) injected into running/suspended chat %s", source, chat_id)
            return {"status": "injected", "message_id": str(injected.id)}

        token = create_access_token(user_id=str(user.id), email=user.email)
        channel_id = str(fixed) if fixed else str(uuid.uuid4())
        agent_label = (
            f"{chat.agent_namespace}/{chat.agent_name}" if chat.agent_namespace and chat.agent_name else None
        )
        job_timeout = chat.job_timeout

        async def enqueue() -> str:
            key_token = bind_api_key(api_key_id)
            try:
                return await queue_service.enqueue_agent_message(
                    chat_id=chat_id,
                    user_id=str(user.id),
                    user_token=token,
                    content=content,
                    channel_id=channel_id,
                    agent=agent_label,
                    trigger_type=source,
                    job_timeout=job_timeout,
                    job_id=fixed_job_id,
                )
            finally:
                reset_api_key(key_token)

        if guard is not None:
            if not await guard(db):
                await db.rollback()
                return {"status": "dropped", "reason": "withdrawn"}
            chat.active_channel_id = channel_id
            job_id = await enqueue()  # inside the guarded transaction
            await db.commit()
        else:
            chat.active_channel_id = channel_id
            await db.commit()

    if guard is None:
        job_id = await enqueue()
    logger.info("Wakeup (%s) started a new turn on chat %s (job %s)", source, chat_id, job_id)
    return {"status": "started", "job_id": job_id, "channel_id": channel_id}


async def _job_state(job_id: str) -> Optional[str]:
    """Where arq has this job: "running", "queued", "finished" (within its
    result retention, an hour by default; delivery retries come within a
    minute), or None when it has no such job."""
    from arq.constants import in_progress_key_prefix, job_key_prefix, result_key_prefix

    from app.core.redis import get_arq_pool

    pool = await get_arq_pool()
    if await pool.exists(f"{in_progress_key_prefix}{job_id}"):
        return "running"
    if await pool.exists(f"{job_key_prefix}{job_id}"):
        return "queued"
    if await pool.exists(f"{result_key_prefix}{job_id}"):
        return "finished"
    return None


# ---------------------------------------------------------------------------
# Timers
# ---------------------------------------------------------------------------

async def schedule_timer(db, chat: Chat, user_id: str, delay_seconds: int, note: str) -> dict[str, Any]:
    from app.core.auth import current_api_key_id

    if delay_seconds < 1:
        return {"error": "delay_seconds must be at least 1"}
    if delay_seconds > settings.wakeup_max_delay_seconds:
        return {"error": f"delay_seconds may be at most {settings.wakeup_max_delay_seconds}"}
    row = ChatWakeup(
        chat_id=chat.id, user_id=uuid.UUID(user_id), kind=TIMER, note=note,
        due_at=_now() + timedelta(seconds=delay_seconds), api_key_id=current_api_key_id(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "wakeup_id": str(row.id), "due_at": _iso(row.due_at), "note": note,
        "hint": "Delivered as a message in this chat when due (minute granularity). "
                "Finish your turn; you are woken then.",
    }


async def cancel_timer(db, chat: Chat, wakeup_id: str) -> dict[str, Any]:
    row = await _owned(db, chat, wakeup_id, TIMER)
    if row is None:
        return {"error": "No such pending wakeup in this chat"}
    row.cancelled = True
    await db.commit()
    return {"wakeup_id": wakeup_id, "cancelled": True}


async def deliver_due_timers(now: Optional[datetime] = None) -> int:
    """Sweep: deliver every timer whose due time has passed. Minutely, from
    the agent worker's cron. Returns how many were delivered.

    A timer is marked delivered in the same transaction that delivers it
    (`deliver_to_chat`'s guard), never before: one whose delivery fails stays
    due and the next sweep tries again. The guard only marks a timer still
    undelivered and uncancelled, so two sweeps never both deliver it, and the
    fixed delivery id keeps a retry after a crash from queuing its turn
    twice."""
    from app.core.database import AsyncSessionLocal

    now = now or _now()
    async with AsyncSessionLocal() as db:
        due = [
            (str(r.chat_id), r.id, r.note, r.api_key_id)
            for r in (
                await db.execute(
                    select(ChatWakeup).where(
                        ChatWakeup.kind == TIMER,
                        ChatWakeup.cancelled.is_(False),
                        ChatWakeup.delivered_at.is_(None),
                        ChatWakeup.due_at <= now,
                    )
                )
            ).scalars().all()
        ]

    delivered = 0
    for chat_id, wakeup_id, note, api_key_id in due:
        async def mark_delivered(session, chat_id=chat_id, wakeup_id=wakeup_id) -> bool:
            # The chat row, then the timer: the order a chat deletion takes them.
            await session.execute(
                select(Chat.id).where(Chat.id == uuid.UUID(chat_id)).with_for_update(key_share=True)
            )
            row = (
                await session.execute(
                    select(ChatWakeup).where(ChatWakeup.id == wakeup_id).with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is None or row.cancelled or row.delivered_at is not None:
                return False
            fired = _now()
            row.delivered_at = fired
            row.fired_count = (row.fired_count or 0) + 1
            row.last_fired_at = fired
            return True

        try:
            outcome = await deliver_to_chat(
                chat_id, f"[wakeup {wakeup_id}] {note or ''}".rstrip(),
                source="wakeup", api_key_id=api_key_id,
                guard=mark_delivered, delivery_id=f"wakeup:{wakeup_id}",
            )
        except Exception:
            logger.exception("Delivering wakeup %s to chat %s failed; retrying next sweep", wakeup_id, chat_id)
            continue
        if outcome.get("status") == "dropped" and outcome.get("reason") != "withdrawn":
            # Refused for good (chat archived, owner inactive, a hook): stop.
            await _retire_timer(wakeup_id)
        elif outcome.get("status") != "dropped":
            delivered += 1
    return delivered


async def _retire_timer(wakeup_id) -> None:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = await db.get(ChatWakeup, wakeup_id, with_for_update=True)
        if row is not None and row.delivered_at is None:
            row.delivered_at = _now()
        await db.commit()


# ---------------------------------------------------------------------------
# Watches
# ---------------------------------------------------------------------------

def watch_url(token: str) -> str:
    return f"{settings.public_base_url}/watch/{token}"


async def create_watch(db, chat: Chat, user_id: str, description: str, ttl_seconds: Optional[int]) -> dict[str, Any]:
    from app.core.auth import current_api_key_id

    ttl = ttl_seconds or settings.watch_default_ttl_seconds
    if ttl < 60:
        return {"error": "expires_in_seconds must be at least 60"}
    ttl = min(ttl, settings.watch_max_ttl_seconds)
    row = ChatWakeup(
        chat_id=chat.id, user_id=uuid.UUID(user_id), kind=WATCH, note=description,
        token=secrets.token_urlsafe(32), expires_at=_now() + timedelta(seconds=ttl),
        api_key_id=current_api_key_id(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "watch_id": str(row.id), "url": watch_url(row.token), "expires_at": _iso(row.expires_at),
        "description": description,
        "hint": "Give this URL to the external system. Each POST to it is delivered "
                "into this chat as a message prefixed [watch <id>]. Finish your turn; "
                "you are woken on delivery.",
    }


async def delete_watch(db, chat: Chat, watch_id: str) -> dict[str, Any]:
    row = await _owned(db, chat, watch_id, WATCH)
    if row is None:
        return {"error": "No such active watch in this chat"}
    row.cancelled = True
    await db.commit()
    return {"watch_id": watch_id, "deleted": True}


async def watch_status(token: str) -> Optional[tuple[int, dict[str, Any]]]:
    """(status, body) when `token` cannot take a delivery (404 unknown, 410
    expired or deleted), else None. Cheap: one indexed lookup, no writes."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(ChatWakeup.cancelled, ChatWakeup.expires_at)
                .where(ChatWakeup.token == token, ChatWakeup.kind == WATCH)
            )
        ).first()
    if row is None:
        return 404, {"detail": "Unknown watch"}
    cancelled, expires_at = row
    if cancelled or (expires_at and expires_at <= _now()):
        return 410, {"detail": "Watch expired or deleted"}
    return None


async def handle_watch_delivery(token: str, payload: Any) -> tuple[int, dict[str, Any]]:
    """POST /watch/{token}: deliver `payload` into the watch's chat.

    Returns (http status, body). 404 unknown token, 410 expired or deleted.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(ChatWakeup).where(ChatWakeup.token == token, ChatWakeup.kind == WATCH))
        ).scalar_one_or_none()
        if row is None:
            return 404, {"detail": "Unknown watch"}
        if row.cancelled or (row.expires_at and row.expires_at <= _now()):
            return 410, {"detail": "Watch expired or deleted"}
        # Counted in the database: two simultaneous deliveries must not
        # both write the same read-then-add value.
        await db.execute(
            update(ChatWakeup)
            .where(ChatWakeup.id == row.id)
            .values(fired_count=func.coalesce(ChatWakeup.fired_count, 0) + 1, last_fired_at=_now())
        )
        await db.commit()
        chat_id, watch_id, description, api_key_id = str(row.chat_id), str(row.id), row.note, row.api_key_id

    rendered = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    outcome = await deliver_to_chat(
        chat_id, f"[watch {watch_id}: {description or ''}]\n{rendered}".rstrip(),
        source="watch", api_key_id=api_key_id,
    )
    status = 202 if outcome["status"] in ("injected", "started") else 409
    return status, {"watch_id": watch_id, "delivery": outcome["status"]}


async def _owned(db, chat: Chat, wakeup_id: str, kind: str) -> Optional[ChatWakeup]:
    try:
        wid = uuid.UUID(wakeup_id)
    except (ValueError, TypeError):
        return None
    row = (
        await db.execute(
            select(ChatWakeup).where(ChatWakeup.id == wid, ChatWakeup.chat_id == chat.id, ChatWakeup.kind == kind)
        )
    ).scalar_one_or_none()
    if row is None or row.cancelled or row.delivered_at is not None:
        return None
    return row


async def list_wakeups(db, chat: Chat) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ChatWakeup)
            .where(ChatWakeup.chat_id == chat.id, ChatWakeup.cancelled.is_(False), ChatWakeup.delivered_at.is_(None))
            .order_by(ChatWakeup.created_at)
        )
    ).scalars().all()
    now = _now()
    timers = [
        {"wakeup_id": str(r.id), "due_at": _iso(r.due_at), "note": r.note}
        for r in rows if r.kind == TIMER
    ]
    watches = [
        {"watch_id": str(r.id), "url": watch_url(r.token), "description": r.note,
         "expires_at": _iso(r.expires_at), "deliveries": r.fired_count}
        for r in rows if r.kind == WATCH and not (r.expires_at and r.expires_at <= now)
    ]
    jobs = await list_background_jobs(str(chat.id))
    from app.services.mcp_tasks import list_tasks

    out = {"timers": timers, "watches": watches, "background_jobs": jobs}
    mcp_tasks = await list_tasks(db, chat)
    if mcp_tasks:
        out["mcp_tasks"] = mcp_tasks
    return out


# ---------------------------------------------------------------------------
# Background code runs
# ---------------------------------------------------------------------------

async def start_background_job(
    chat_id: str, user_id: str, code: str, description: str,
    *, tool_call_id: Optional[str] = None, pending_completion_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    """Queue a sandbox run. Without `pending_completion_id` the result is
    delivered into the chat when done; with it, the result completes that
    checkpoint entry (the round suspended on it)."""
    from app.core.auth import current_api_key_id
    from app.core.redis import get_arq_pool, get_redis
    from app.core.telemetry import inject_job_context

    if not settings.code_execution_enabled:
        return {"error": "Code execution is disabled on this deployment."}
    if not code or not code.strip():
        return {"error": "run_in_background requires non-empty 'code'"}

    # The caller may fix the id (a waited-on run records it in the
    # checkpoint so a timeout message can point at a real status record).
    job_id = job_id or str(uuid.uuid4())
    redis = await get_redis()
    await redis.set(
        f"{BG_JOB_PREFIX}{job_id}",
        json.dumps({"status": "queued", "description": description, "chat_id": chat_id,
                    "queued_at": _now().isoformat(), "wait": pending_completion_id is not None}),
        ex=BG_JOB_TTL,
    )
    # One MULTI: never a chat's job index without its TTL.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.sadd(f"{CHAT_BG_JOBS_PREFIX}{chat_id}", job_id)
        pipe.expire(f"{CHAT_BG_JOBS_PREFIX}{chat_id}", BG_JOB_TTL)
        await pipe.execute()

    pool = await get_arq_pool()
    await pool.enqueue_job(
        "execute_background_code_job",
        job_id=job_id, chat_id=chat_id, user_id=user_id, code=code, description=description,
        tool_call_id=tool_call_id, pending_completion_id=pending_completion_id,
        api_key_id=current_api_key_id(), trace_context=inject_job_context(),
        # Timed by the function's own arq timeout (WorkerSettings): arq's
        # enqueue_job takes no per-job timeout.
        _job_id=job_id, _queue_name=BACKGROUND_QUEUE,
    )
    out = {"job_id": job_id, "status": "queued", "description": description}
    if pending_completion_id is None:
        out["hint"] = ("Runs in the background. When it finishes, its result is delivered "
                       "into this chat as a message prefixed [background job <id> finished]. "
                       "Carry on with other work or finish your turn; check with "
                       "background_job_status if you need to.")
    return out


async def background_job_status(chat_id: str, job_id: str) -> dict[str, Any]:
    from app.core.redis import get_redis

    redis = await get_redis()
    raw = await redis.get(f"{BG_JOB_PREFIX}{job_id}")
    if not raw:
        return {"error": "Unknown or expired background job id"}
    data = json.loads(raw)
    if data.get("chat_id") != chat_id:
        return {"error": "Unknown or expired background job id"}
    return {"job_id": job_id, **{k: v for k, v in data.items() if k != "chat_id"}}


async def list_background_jobs(chat_id: str) -> list[dict[str, Any]]:
    from app.core.redis import get_redis

    redis = await get_redis()
    ids = await redis.smembers(f"{CHAT_BG_JOBS_PREFIX}{chat_id}")
    out = []
    for job_id in sorted(ids or []):
        raw = await redis.get(f"{BG_JOB_PREFIX}{job_id}")
        if raw:
            d = json.loads(raw)
            out.append({"job_id": job_id, "status": d.get("status"), "description": d.get("description")})
    return out


async def run_background_code_job(
    *, job_id: str, chat_id: str, user_id: str, code: str, description: str,
    tool_call_id: Optional[str], pending_completion_id: Optional[str], api_key_id: Optional[str],
) -> None:
    """Worker body for execute_background_code_job."""
    from app.core.auth import bind_api_key, create_access_token, reset_api_key
    from app.core.database import AsyncSessionLocal
    from app.core.redis import get_redis
    from app.services import code_execution

    redis = await get_redis()

    async def set_status(**fields):
        raw = await redis.get(f"{BG_JOB_PREFIX}{job_id}")
        data = json.loads(raw) if raw else {"description": description, "chat_id": chat_id}
        data.update(fields)
        await redis.set(f"{BG_JOB_PREFIX}{job_id}", json.dumps(data, default=str), ex=BG_JOB_TTL)

    await set_status(status="running", started_at=_now().isoformat())

    async def report(status: str, result: dict[str, Any]) -> None:
        await set_status(status=status, finished_at=_now().isoformat(), result=result)
        content = json.dumps(result, default=str)
        limit = settings.background_job_result_max_chars
        if len(content) > limit:
            # The full result stays behind background_job_status; what goes
            # into the model request is bounded like any tool result.
            content = json.dumps({
                "job_id": job_id,
                "status": status,
                "truncated": True,
                "result_preview": content[:limit],
                "hint": f"Result clipped ({len(content) - limit} more chars); "
                        f"the full result is available via background_job_status('{job_id}').",
            })
        if pending_completion_id and tool_call_id:
            from app.services import deferred_completions

            async with AsyncSessionLocal() as db:
                user = (await db.execute(select(User).where(User.id == uuid.UUID(user_id)))).scalar_one_or_none()
            token = create_access_token(user_id=user_id, email=user.email if user else "")
            await deferred_completions.complete(
                pending_completion_id, tool_call_id, content, user_token=token
            )
        else:
            await deliver_to_chat(
                chat_id,
                f"[background job {job_id} {status}: {description}]\n{content}",
                source="background_job", api_key_id=api_key_id,
            )

    key_token = bind_api_key(api_key_id)
    try:
        try:
            result = await code_execution.execute(code, user_id=user_id, chat_id=chat_id)
            status = "failed" if result.get("error") else "finished"
        except asyncio.CancelledError:
            # Worker timeout or shutdown: a job must never leave a round
            # parked or a chat waiting for a message that is not coming.
            logger.warning("Background job %s cancelled", job_id)
            await asyncio.shield(report("failed", {"error": "Background run was cancelled (worker timeout or shutdown)"}))
            raise
        except Exception as e:  # the run itself blew up: still report back
            logger.exception("Background job %s crashed", job_id)
            result = {"error": str(e)}
            status = "failed"
        if status == "failed":
            from opentelemetry import trace

            from app.core.telemetry import mark_error

            mark_error(trace.get_current_span(), f"Background run failed: {result.get('error')}", "job_failed")
        await report(status, result)
    finally:
        reset_api_key(key_token)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def get_background_tool_definitions(code_execution_available: bool) -> list[dict[str, Any]]:
    meta = {"system_tool": True, "tool_type": "background"}
    tools: list[dict[str, Any]] = []
    if code_execution_available:
        tools.append({
            "type": "function",
            "function": {
                "name": RUN_IN_BACKGROUND,
                "description": (
                    "Run Python code in the sandbox without blocking: the call returns a "
                    "job id at once and the result is delivered into this chat as a message "
                    "when the run finishes, so you can keep working or end your turn. "
                    "Use it for anything that takes more than a few seconds. With wait=true "
                    "this conversation pauses until the run finishes and the result comes "
                    "back as this tool's result (no worker is held meanwhile). Same sandbox "
                    "as execute_code: the workbench is materialized and files written "
                    "persist back."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to run."},
                        "description": {"type": "string", "description": "What this run does, in a few words (shown with the result)."},
                        "wait": {"type": "boolean", "description": "Pause this conversation until the run finishes (default false)."},
                    },
                    "required": ["code", "description"],
                },
                # Deferred only when the model asks to wait — see
                # message_service._handle_tool_calls (deferred_if_arg).
                "_metadata": {**meta, "deferred_completer": "background_job", "deferred_if_arg": "wait"},
            },
        })
        tools.append({
            "type": "function",
            "function": {
                "name": BACKGROUND_JOB_STATUS,
                "description": "Status and (once finished) full result of a background job started with run_in_background.",
                "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
                "_metadata": meta,
            },
        })
    tools.extend([
        {
            "type": "function",
            "function": {
                "name": SCHEDULE_WAKEUP,
                "description": (
                    "Wake yourself later: after delay_seconds a message '[wakeup <id>] <note>' "
                    "is delivered into this chat and you continue from it. Use it to check on "
                    "something external (a deploy, a long job elsewhere) instead of polling; "
                    "end your turn after scheduling. Minute granularity."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delay_seconds": {"type": "integer", "minimum": 1},
                        "note": {"type": "string", "description": "What to do when woken; you will read this cold."},
                    },
                    "required": ["delay_seconds", "note"],
                },
                "_metadata": meta,
            },
        },
        {
            "type": "function",
            "function": {
                "name": CANCEL_WAKEUP,
                "description": "Cancel a pending wakeup timer by id.",
                "parameters": {"type": "object", "properties": {"wakeup_id": {"type": "string"}}, "required": ["wakeup_id"]},
                "_metadata": meta,
            },
        },
        {
            "type": "function",
            "function": {
                "name": CREATE_WATCH,
                "description": (
                    "Create a callback URL for this chat. Hand it to an external system "
                    "(a CI pipeline, a webhook sender, another agent); every POST to it is "
                    "delivered into this chat as a message '[watch <id>: <description>]' "
                    "followed by the payload, and you continue from it. The URL is the "
                    "secret; it expires after expires_in_seconds (default one day)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "What deliveries on this URL mean, in a few words."},
                        "expires_in_seconds": {"type": "integer", "minimum": 60},
                    },
                    "required": ["description"],
                },
                "_metadata": meta,
            },
        },
        {
            "type": "function",
            "function": {
                "name": DELETE_WATCH,
                "description": "Delete a watch URL by id; later POSTs to it are rejected.",
                "parameters": {"type": "object", "properties": {"watch_id": {"type": "string"}}, "required": ["watch_id"]},
                "_metadata": meta,
            },
        },
        {
            "type": "function",
            "function": {
                "name": LIST_WAKEUPS,
                "description": "List this chat's pending wakeup timers, active watch URLs, recent background jobs and running MCP tasks.",
                "parameters": {"type": "object", "properties": {}},
                "_metadata": meta,
            },
        },
    ])
    return tools


async def execute_background_tool(db, chat: Chat, user_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if chat is None:
        return {"error": "Background tools need a chat"}
    if tool_name == RUN_IN_BACKGROUND:
        if arguments.get("wait"):
            # Every chat tool round splits a waited-on run off and suspends
            # on it (message_service), queued or inline; only a caller
            # outside a chat tool round gets here. Say so rather than
            # returning a handle the model did not ask for.
            return {
                "error": (
                    "wait=true can only be used in a chat turn, which suspends until "
                    "the run finishes. Call again with wait=false and you will be sent "
                    "the result, or poll background_job_status."
                )
            }
        return await start_background_job(
            str(chat.id), user_id, arguments.get("code", ""), arguments.get("description") or "background run",
        )
    if tool_name == BACKGROUND_JOB_STATUS:
        return await background_job_status(str(chat.id), str(arguments.get("job_id", "")))
    if tool_name == SCHEDULE_WAKEUP:
        try:
            delay = int(arguments.get("delay_seconds"))
        except (TypeError, ValueError):
            return {"error": "delay_seconds must be an integer"}
        return await schedule_timer(db, chat, user_id, delay, str(arguments.get("note") or ""))
    if tool_name == CANCEL_WAKEUP:
        return await cancel_timer(db, chat, str(arguments.get("wakeup_id", "")))
    if tool_name == CREATE_WATCH:
        ttl = arguments.get("expires_in_seconds")
        try:
            ttl = int(ttl) if ttl is not None else None
        except (TypeError, ValueError):
            return {"error": "expires_in_seconds must be an integer"}
        return await create_watch(db, chat, user_id, str(arguments.get("description") or ""), ttl)
    if tool_name == DELETE_WATCH:
        return await delete_watch(db, chat, str(arguments.get("watch_id", "")))
    if tool_name == LIST_WAKEUPS:
        return await list_wakeups(db, chat)
    return {"error": f"Unknown background tool {tool_name}"}
