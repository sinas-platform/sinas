"""Deferred tool rounds — the pending-completion checkpoint and its completers.

Covers the unified suspend/complete/expire machinery that
suspend-on-delegate (issue #90) and the ask_user tool are both cases of:

- checkpoint shape: one row per suspended round, mixed completer kinds,
  row-level expires_at = earliest entry deadline;
- complete(): tool result persisted BEFORE the counter moves (an assistant
  tool_calls message must never be left without matching results when the
  round resumes), duplicate deliveries are no-ops, the last completion
  enqueues the resume job;
- expire_due(): overdue entries resolve with their completer's timeout
  content through the same complete() path, stale approvals auto-reject;
- the delegation wrappers still present their pre-unification surface.

These tests run against the real Postgres + Redis stack. The service opens
its own DB sessions, so fixtures commit real rows and clean them up.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, async_engine
from app.models.chat import Chat, Message
from app.models.pending_approval import PendingToolApproval
from app.models.pending_completion import PendingCompletion
from app.models.user import User
from app.services import deferred_completions as dc
from app.services.queue_service import queue_service


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def test_registry_has_both_shipped_completers():
    assert dc.get_completer(dc.SUB_AGENT).kind == dc.SUB_AGENT
    assert dc.get_completer(dc.HUMAN_INPUT).kind == dc.HUMAN_INPUT


def test_unknown_completer_kind_degrades_gracefully():
    # A checkpoint written by newer code must not wedge an older worker:
    # unknown kinds get a generic completer with a JSON timeout error.
    completer = dc.get_completer("holographic_input")
    assert completer.tool_message_name({}) is None
    timeout = json.loads(completer.timeout_content("call_1", {}))
    assert "error" in timeout


def test_entry_kind_defaults_to_sub_agent_for_legacy_rows():
    # pending_delegations rows written before the unification have no
    # "completer" key — they were all delegations.
    assert dc.entry_kind({"sub_chat_id": "x", "agent": "ns/a"}) == dc.SUB_AGENT
    assert dc.entry_kind({"completer": "human_input"}) == dc.HUMAN_INPUT


def test_sub_agent_tool_message_name_matches_call_agent_convention():
    name = dc.get_completer(dc.SUB_AGENT).tool_message_name({"agent": "ns/helper"})
    assert name == "call_agent_ns__helper"


def test_human_input_timeout_content_is_actionable_json():
    content = json.loads(
        dc.get_completer(dc.HUMAN_INPUT).timeout_content("call_1", {"question": "?"})
    )
    assert content["timed_out"] is True
    assert "error" in content


def test_deadline_from_now_disabled_for_nonpositive_timeouts():
    assert dc.deadline_from_now(0) is None
    assert dc.deadline_from_now(-5) is None
    deadline = datetime.fromisoformat(dc.deadline_from_now(60))
    assert deadline > datetime.now(timezone.utc)


def test_suspension_event_types_cover_the_legacy_delegation_event():
    # agent jobs decide "don't publish done" off this set; the legacy
    # delegation event must stay in it or suspend-mode delegation breaks.
    assert "delegation_pending" in dc.SUSPENSION_EVENT_TYPES
    assert dc.ROUND_SUSPENDED in dc.SUSPENSION_EVENT_TYPES


# ---------------------------------------------------------------------------
# Checkpoint lifecycle (real DB — committed fixtures, explicit cleanup)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    """The module-level engine pools connections bound to each test's event
    loop; drop them after every test so the next loop starts clean."""
    yield
    await async_engine.dispose()


@pytest_asyncio.fixture
async def env():
    """A committed user + chat, visible to the service's own DB sessions."""
    async with AsyncSessionLocal() as s:
        user = User(email=f"deferred-{uuid.uuid4().hex[:8]}@example.com")
        s.add(user)
        await s.flush()
        chat = Chat(user_id=user.id, title="deferred round test")
        s.add(chat)
        await s.commit()
        env = {"user_id": str(user.id), "chat_id": str(chat.id)}

    yield env

    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(PendingToolApproval).where(PendingToolApproval.chat_id == env["chat_id"])
        )
        await s.execute(
            delete(PendingCompletion).where(PendingCompletion.chat_id == env["chat_id"])
        )
        await s.execute(delete(Message).where(Message.chat_id == env["chat_id"]))
        await s.execute(delete(Chat).where(Chat.id == env["chat_id"]))
        await s.execute(delete(User).where(User.id == env["user_id"]))
        await s.commit()


@pytest.fixture
def capture_resume(monkeypatch):
    """Capture resume-job enqueues instead of hitting the real queue."""
    calls: list[dict] = []

    async def fake(**kwargs):
        calls.append(kwargs)
        return "job-id"

    monkeypatch.setattr(queue_service, "enqueue_agent_delegate_resume", fake)
    return calls


async def _suspend(env, entries, context=None):
    async with AsyncSessionLocal() as s:
        row = await dc.suspend_round(
            s,
            chat_id=env["chat_id"],
            user_id=env["user_id"],
            channel_id="chan-orig",
            entries=entries,
            conversation_context=context or {"provider": "p", "model": "m"},
        )
        return str(row.id)


async def _get_row(row_id):
    async with AsyncSessionLocal() as s:
        return (
            await s.execute(select(PendingCompletion).where(PendingCompletion.id == row_id))
        ).scalar_one_or_none()


async def _tool_messages(chat_id):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Message)
            .where(Message.chat_id == chat_id, Message.role == "tool")
            .order_by(Message.created_at)
        )
        return result.scalars().all()


PAST = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
FUTURE = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


@pytest.mark.asyncio
async def test_suspend_round_persists_mixed_checkpoint(env):
    row_id = await _suspend(
        env,
        {
            "call_ask": {
                "completer": dc.HUMAN_INPUT,
                "question": "Which region?",
                "options": ["eu", "us"],
                "expires_at": FUTURE,
            },
            "call_del": {"completer": dc.SUB_AGENT, "sub_chat_id": "sc", "agent": "ns/a"},
        },
    )
    row = await _get_row(row_id)
    assert row.remaining == 2
    assert set(row.pending) == {"call_ask", "call_del"}
    # Row deadline is the earliest entry deadline (the delegation entry has
    # none, so the ask deadline wins).
    assert row.expires_at == datetime.fromisoformat(FUTURE)


@pytest.mark.asyncio
async def test_complete_persists_result_before_resuming(env, capture_resume):
    row_id = await _suspend(
        env,
        {
            "call_ask": {"completer": dc.HUMAN_INPUT, "question": "?", "expires_at": FUTURE},
            "call_del": {"completer": dc.SUB_AGENT, "sub_chat_id": "sc", "agent": "ns/a"},
        },
    )

    outcome = await dc.complete(
        row_id, "call_ask", json.dumps({"answer": "eu"}), user_token="tok"
    )
    assert outcome == {"status": "completed", "resumed": False}
    assert capture_resume == []  # one completion still outstanding

    # The tool result row exists NOW — the follow-up turn rebuilds from it.
    messages = await _tool_messages(env["chat_id"])
    assert [m.tool_call_id for m in messages] == ["call_ask"]
    assert messages[0].name == "ask_user"
    assert json.loads(messages[0].content) == {"answer": "eu"}

    row = await _get_row(row_id)
    assert row.remaining == 1
    assert "call_ask" not in row.pending
    # Deadline recomputed: the remaining delegation entry has none.
    assert row.expires_at is None


@pytest.mark.asyncio
async def test_last_completion_deletes_checkpoint_and_resumes(env, capture_resume):
    context = {"provider": "p", "model": "m", "delegation_depth": 0}
    row_id = await _suspend(
        env,
        {
            "call_ask": {"completer": dc.HUMAN_INPUT, "question": "?"},
            "call_del": {"completer": dc.SUB_AGENT, "sub_chat_id": "sc", "agent": "ns/a"},
        },
        context=context,
    )

    await dc.complete(row_id, "call_del", json.dumps({"response": "hi"}), user_token="tok")
    outcome = await dc.complete(
        row_id,
        "call_ask",
        json.dumps({"answer": "yes"}),
        user_token="tok",
        resume_channel_id="chan-fresh",
    )
    assert outcome["resumed"] is True
    assert outcome["channel_id"] == "chan-fresh"

    assert await _get_row(row_id) is None
    # Both tool results persisted, delegation under its call_agent_* name.
    names = {m.tool_call_id: m.name for m in await _tool_messages(env["chat_id"])}
    assert names == {"call_del": "call_agent_ns__a", "call_ask": "ask_user"}

    (resume,) = capture_resume
    assert resume["chat_id"] == env["chat_id"]
    assert resume["channel_id"] == "chan-fresh"  # API caller's fresh channel wins
    assert resume["conversation_context"] == context
    assert resume["user_token"] == "tok"


@pytest.mark.asyncio
async def test_resume_without_fresh_channel_uses_the_suspended_rounds_channel(
    env, capture_resume
):
    row_id = await _suspend(
        env, {"call_del": {"completer": dc.SUB_AGENT, "sub_chat_id": "sc", "agent": "ns/a"}}
    )
    await dc.complete(row_id, "call_del", "{}", user_token="tok")
    (resume,) = capture_resume
    assert resume["channel_id"] == "chan-orig"


@pytest.mark.asyncio
async def test_duplicate_completion_is_a_noop(env, capture_resume):
    row_id = await _suspend(
        env,
        {
            "call_a": {"completer": dc.HUMAN_INPUT, "question": "?"},
            "call_b": {"completer": dc.HUMAN_INPUT, "question": "??"},
        },
    )
    await dc.complete(row_id, "call_a", "{}", user_token="tok")
    outcome = await dc.complete(row_id, "call_a", "{}", user_token="tok")

    # A double delivery must not double-decrement (that would resume a round
    # whose other completion never arrived) or duplicate the tool result.
    assert outcome == {"status": "unknown_tool_call", "resumed": False}
    assert (await _get_row(row_id)).remaining == 1
    assert len(await _tool_messages(env["chat_id"])) == 1
    assert capture_resume == []


@pytest.mark.asyncio
async def test_completion_against_missing_checkpoint_reports_not_found(capture_resume):
    outcome = await dc.complete(str(uuid.uuid4()), "call_x", "{}", user_token="tok")
    assert outcome == {"status": "not_found", "resumed": False}
    assert capture_resume == []


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expire_due_times_out_only_overdue_entries(env, capture_resume):
    row_id = await _suspend(
        env,
        {
            "call_late": {"completer": dc.HUMAN_INPUT, "question": "?", "expires_at": PAST},
            "call_ok": {"completer": dc.HUMAN_INPUT, "question": "??", "expires_at": FUTURE},
        },
    )

    resolved = await dc.expire_due()

    assert resolved == 1
    row = await _get_row(row_id)
    assert set(row.pending) == {"call_ok"}
    assert row.expires_at == datetime.fromisoformat(FUTURE)
    (msg,) = await _tool_messages(env["chat_id"])
    assert msg.tool_call_id == "call_late"
    assert json.loads(msg.content)["timed_out"] is True
    assert capture_resume == []  # round still waiting on the live entry


@pytest.mark.asyncio
async def test_expiring_the_last_entry_resumes_the_round(env, capture_resume):
    row_id = await _suspend(
        env,
        {"call_del": {"completer": dc.SUB_AGENT, "sub_chat_id": "sc", "agent": "ns/a", "expires_at": PAST}},
    )

    await dc.expire_due()

    assert await _get_row(row_id) is None
    (msg,) = await _tool_messages(env["chat_id"])
    assert "deadline" in json.loads(msg.content)["error"]
    (resume,) = capture_resume
    assert resume["chat_id"] == env["chat_id"]
    # No user token exists at expiry time; the resume runs without one.
    assert resume["user_token"] == ""


@pytest.mark.asyncio
async def test_entries_without_deadline_never_expire(env, capture_resume):
    row_id = await _suspend(
        env, {"call_ask": {"completer": dc.HUMAN_INPUT, "question": "?"}}
    )
    await dc.expire_due()
    assert (await _get_row(row_id)).remaining == 1
    assert capture_resume == []


@pytest.mark.asyncio
async def test_expire_due_auto_rejects_stale_approvals(env):
    async with AsyncSessionLocal() as s:
        anchor = Message(chat_id=env["chat_id"], role="assistant", content="x")
        s.add(anchor)
        await s.flush()

        def approval(tool_call_id, expires_at):
            return PendingToolApproval(
                chat_id=env["chat_id"],
                message_id=anchor.id,
                user_id=env["user_id"],
                tool_call_id=tool_call_id,
                function_namespace="ns",
                function_name="fn",
                arguments={},
                all_tool_calls=[],
                conversation_context={},
                expires_at=expires_at,
            )

        stale_id = f"appr-stale-{uuid.uuid4().hex[:6]}"
        fresh_id = f"appr-fresh-{uuid.uuid4().hex[:6]}"
        eternal_id = f"appr-eternal-{uuid.uuid4().hex[:6]}"
        s.add(approval(stale_id, datetime.fromisoformat(PAST)))
        s.add(approval(fresh_id, datetime.fromisoformat(FUTURE)))
        s.add(approval(eternal_id, None))  # pre-expiry rows: NULL = ask forever
        await s.commit()

    await dc.expire_due()

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(PendingToolApproval).where(
                    PendingToolApproval.chat_id == env["chat_id"]
                )
            )
        ).scalars().all()
        by_id = {r.tool_call_id: r.approved for r in rows}
    assert by_id[stale_id] is False  # auto-rejected, same terminal state as a user "no"
    assert by_id[fresh_id] is None
    assert by_id[eternal_id] is None


# ---------------------------------------------------------------------------
# Delegation is now a case of this — the wrappers keep their old surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_delegations_checkpoints_then_enqueues_children(
    env, monkeypatch, capture_resume
):
    enqueued: list[dict] = []

    async def fake_enqueue(**kwargs):
        # The checkpoint row must exist before any child job does — a fast
        # child reports completion against it.
        assert await _get_row(kwargs["pending_delegation_id"]) is not None
        enqueued.append(kwargs)
        return "job-id"

    monkeypatch.setattr(queue_service, "enqueue_agent_message", fake_enqueue)

    from app.services.delegation import on_child_complete, suspend_delegations

    async with AsyncSessionLocal() as s:
        row_id = await suspend_delegations(
            s,
            chat_id=env["chat_id"],
            user_id=env["user_id"],
            user_token="tok",
            channel_id="chan-orig",
            delegations=[
                {"tool_call_id": "call_d1", "sub_chat_id": "sc1", "agent": "ns/a", "content": "go"},
            ],
            conversation_context={"delegation_depth": 1},
        )

    assert [e["chat_id"] for e in enqueued] == ["sc1"]
    assert enqueued[0]["depth"] == 2  # parent depth + 1
    assert enqueued[0]["parent_tool_call_id"] == "call_d1"

    row = await _get_row(row_id)
    assert dc.entry_kind(row.pending["call_d1"]) == dc.SUB_AGENT

    # The pre-unification completion entry point still resumes the round.
    await on_child_complete(row_id, "call_d1", json.dumps({"response": "done"}), user_token="tok")
    assert await _get_row(row_id) is None
    (resume,) = capture_resume
    assert resume["conversation_context"] == {"delegation_depth": 1}
