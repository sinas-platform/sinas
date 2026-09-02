"""The ask_user system tool — a tool round that suspends on human input.

The user-visible case of the deferred-completion machinery: an agent with
`system_tools: ["askUser"]` can ask the user a question mid-turn; the round
suspends (no LLM follow-up), the question surfaces as an `input_required`
stream event and via the pending-input API, and the posted answer becomes
the tool result the round resumes with.

Adversarial coverage the resume path must survive:
- the assistant tool_calls message is persisted before suspension, and the
  answer's tool result lands before the resume job runs (transcript
  validity);
- a cooperative interrupt beats the suspension — synthetic results are
  written for every call, ask_user included, and no checkpoint is left;
- a malformed ask_user call (no question) fails immediately rather than
  parking the round on a question nobody can see.
"""
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, async_engine
from app.models.chat import Chat, Message
from app.models.pending_completion import PendingCompletion
from app.models.user import Role, RolePermission, User, UserRole
from app.services import deferred_completions as dc
from app.services.ask_user_tools import get_ask_user_tool_definition
from app.services.delegation import current_channel_id
from app.services.message_service import MessageService
from app.services.queue_service import queue_service

from tests.conftest import auth_headers


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_per_test():
    yield
    await async_engine.dispose()


# ---------------------------------------------------------------------------
# Tool definition and discovery binding
# ---------------------------------------------------------------------------


def test_tool_definition_is_marked_deferred():
    fn = get_ask_user_tool_definition()["function"]
    assert fn["name"] == "ask_user"
    assert fn["_metadata"]["deferred_completer"] == dc.HUMAN_INPUT
    assert fn["parameters"]["required"] == ["question"]


@pytest.mark.asyncio
async def test_discovery_binds_ask_user_only_when_opted_in(db: AsyncSession, test_user):
    from app.models.agent import Agent
    from app.services.tool_discovery import get_available_tools

    async def tools_for(system_tools):
        agent = Agent(
            namespace="test",
            name=f"asker-{uuid.uuid4().hex[:6]}",
            user_id=test_user.id,
            system_prompt="x",
            system_tools=system_tools,
        )
        db.add(agent)
        await db.flush()
        chat = Chat(user_id=test_user.id, agent_id=agent.id, title="t")
        db.add(chat)
        await db.flush()

        svc = MessageService(db)
        tools = await get_available_tools(
            db=db,
            user_id=str(test_user.id),
            chat=chat,
            function_converter=svc.function_converter,
            query_converter=svc.query_converter,
            skill_converter=svc.skill_converter,
            component_converter=svc.component_converter,
            collection_converter=svc.collection_converter,
            connector_converter=svc.connector_converter,
        )
        return {t["function"]["name"] for t in tools}

    assert "ask_user" in await tools_for(["askUser"])
    assert "ask_user" not in await tools_for([])


# ---------------------------------------------------------------------------
# Round suspension in _handle_tool_calls
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def chat(db: AsyncSession, test_user: User) -> Chat:
    c = Chat(user_id=test_user.id, title="ask_user test chat")
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


def _ask_call(call_id: str, question="Which region?", **extra):
    args = {"question": question, **extra} if question is not None else extra
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "ask_user", "arguments": json.dumps(args)},
    }


async def _run_round(db, chat, test_user, tool_calls, channel="chan-test"):
    svc = MessageService(db)
    token = current_channel_id.set(channel)
    chunks = []
    try:
        async for chunk in svc._handle_tool_calls(
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            user_token="tok",
            messages=[],
            tool_calls=tool_calls,
            provider="openai",
            model="gpt-test",
            temperature=0.7,
            max_tokens=None,
            tools=[get_ask_user_tool_definition()],
            permissions={},
        ):
            chunks.append(chunk)
    finally:
        current_channel_id.reset(token)
    return chunks


@pytest.mark.asyncio
async def test_ask_user_round_suspends_instead_of_executing(db, chat, test_user):
    chunks = await _run_round(
        db, chat, test_user, [_ask_call("call_q1", options=["eu", "us"])]
    )

    by_type = {c.get("type"): c for c in chunks}
    assert "input_required" in by_type
    assert by_type["input_required"]["question"] == "Which region?"
    assert by_type["input_required"]["options"] == ["eu", "us"]
    assert by_type["input_required"]["expires_at"] is not None  # default timeout armed
    assert dc.ROUND_SUSPENDED in by_type
    assert by_type[dc.ROUND_SUSPENDED]["tool_call_ids"] == ["call_q1"]

    # Checkpoint persisted with the question, resumable by the answer API.
    row = (
        await db.execute(
            select(PendingCompletion).where(PendingCompletion.chat_id == chat.id)
        )
    ).scalar_one()
    assert dc.entry_kind(row.pending["call_q1"]) == dc.HUMAN_INPUT
    assert row.pending["call_q1"]["question"] == "Which region?"
    assert str(row.id) == by_type[dc.ROUND_SUSPENDED]["pending_completion_id"]

    # Transcript state at suspension: assistant tool_calls message persisted,
    # tool result deliberately absent until the answer lands.
    messages = (
        await db.execute(
            select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
        )
    ).scalars().all()
    assert [m.role for m in messages] == ["assistant"]
    assert messages[0].tool_calls[0]["id"] == "call_q1"


@pytest.mark.asyncio
async def test_malformed_question_fails_fast_and_does_not_park_the_round(
    db, chat, test_user
):
    chunks = await _run_round(
        db,
        chat,
        test_user,
        [_ask_call("call_bad", question=None), _ask_call("call_good")],
    )

    ends = {c["tool_call_id"]: c for c in chunks if c.get("type") == "tool_end"}
    assert "error" in json.loads(ends["call_bad"]["result"])

    # The malformed call got an immediate error result; the round still
    # suspends on the well-formed question only.
    row = (
        await db.execute(
            select(PendingCompletion).where(PendingCompletion.chat_id == chat.id)
        )
    ).scalar_one()
    assert set(row.pending) == {"call_good"}


@pytest.mark.asyncio
async def test_interrupt_beats_suspension_and_leaves_no_dangling_calls(
    db, chat, test_user
):
    from app.services import chat_steering

    await chat_steering.request_interrupt(str(chat.id))
    chunks = await _run_round(db, chat, test_user, [_ask_call("call_q1")])

    assert any(c.get("type") == "interrupted" for c in chunks)
    # No checkpoint — and the ask_user call has a synthetic tool result, so
    # the assistant tool_calls message is never left unmatched.
    assert (
        await db.execute(
            select(PendingCompletion).where(PendingCompletion.chat_id == chat.id)
        )
    ).scalar_one_or_none() is None
    tool_msgs = (
        await db.execute(
            select(Message).where(Message.chat_id == chat.id, Message.role == "tool")
        )
    ).scalars().all()
    assert [m.tool_call_id for m in tool_msgs] == ["call_q1"]


@pytest.mark.asyncio
async def test_without_a_stream_channel_ask_user_errors_inline(db, chat, test_user):
    # Synchronous paths (no job channel) can't park a conversation on a
    # human — execute_single_tool returns an explanatory error result.
    from app.services.tool_execution import execute_single_tool

    svc = MessageService(db)
    assert current_channel_id.get() is None
    tool_call_id, name, content = await execute_single_tool(
        _ask_call("call_sync"),
        str(chat.id),
        str(test_user.id),
        "tok",
        [get_ask_user_tool_definition()],
        svc.function_converter,
        svc.query_converter,
        svc.skill_converter,
        svc.component_converter,
        svc.collection_converter,
        svc.create_chat_with_agent,
    )
    assert name == "ask_user"
    assert "error" in json.loads(content)


# ---------------------------------------------------------------------------
# The pending-input API
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def committed_user_chat():
    """Committed user (wildcard role) + chat, for endpoints whose service
    layer opens its own DB sessions."""
    async with AsyncSessionLocal() as s:
        user = User(email=f"ask-api-{uuid.uuid4().hex[:8]}@example.com")
        s.add(user)
        await s.flush()
        role = Role(name=f"ask-api-role-{uuid.uuid4().hex[:8]}", description="t")
        s.add(role)
        await s.flush()
        s.add(RolePermission(role_id=role.id, permission_key="sinas.*:all", permission_value=True))
        s.add(UserRole(role_id=role.id, user_id=user.id, active=True))
        chat = Chat(user_id=user.id, title="ask api chat")
        s.add(chat)
        await s.commit()
        await s.refresh(user)
        await s.refresh(chat)
        env = {"user": user, "chat": chat}

    yield env

    async with AsyncSessionLocal() as s:
        chat_id, user_id, role_id = env["chat"].id, env["user"].id, role.id
        await s.execute(delete(PendingCompletion).where(PendingCompletion.chat_id == chat_id))
        await s.execute(delete(Message).where(Message.chat_id == chat_id))
        await s.execute(delete(Chat).where(Chat.id == chat_id))
        await s.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await s.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        await s.execute(delete(Role).where(Role.id == role_id))
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()


async def _suspend_on_question(env, tool_call_id="call_q1", question="Deploy where?"):
    async with AsyncSessionLocal() as s:
        row = await dc.suspend_round(
            s,
            chat_id=str(env["chat"].id),
            user_id=str(env["user"].id),
            channel_id="chan-orig",
            entries={
                tool_call_id: {
                    "completer": dc.HUMAN_INPUT,
                    "question": question,
                    "options": ["eu", "us"],
                }
            },
            conversation_context={"provider": "p", "model": "m"},
        )
        return str(row.id)


@pytest.mark.asyncio
async def test_pending_input_is_queryable(client, committed_user_chat):
    env = committed_user_chat
    await _suspend_on_question(env)

    resp = await client.get(
        f"/chats/{env['chat'].id}/pending-input",
        headers=auth_headers(env["user"]),
    )
    assert resp.status_code == 200
    (item,) = resp.json()
    assert item["tool_call_id"] == "call_q1"
    assert item["question"] == "Deploy where?"
    assert item["options"] == ["eu", "us"]

    # Also surfaced on the chat detail response.
    resp = await client.get(
        f"/chats/{env['chat'].id}", headers=auth_headers(env["user"])
    )
    assert resp.status_code == 200
    assert [p["tool_call_id"] for p in resp.json()["pending_inputs"]] == ["call_q1"]


@pytest.mark.asyncio
async def test_answer_resumes_the_round(client, committed_user_chat, monkeypatch):
    env = committed_user_chat
    await _suspend_on_question(env)

    resumes = []

    async def fake(**kwargs):
        resumes.append(kwargs)
        return "job-id"

    monkeypatch.setattr(queue_service, "enqueue_agent_delegate_resume", fake)

    resp = await client.post(
        f"/chats/{env['chat'].id}/pending-input/call_q1",
        json={"answer": "eu, the data may not leave the region"},
        headers=auth_headers(env["user"]),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "answered"
    assert body["resumed"] is True
    assert body["channel_id"]  # fresh channel for the client to reconnect to

    # The answer is the tool result the follow-up turn is rebuilt from.
    async with AsyncSessionLocal() as s:
        (msg,) = (
            await s.execute(
                select(Message).where(
                    Message.chat_id == env["chat"].id, Message.role == "tool"
                )
            )
        ).scalars().all()
    assert msg.tool_call_id == "call_q1"
    assert json.loads(msg.content)["answer"].startswith("eu")

    (resume,) = resumes
    assert resume["channel_id"] == body["channel_id"]

    # Second submit: the question is gone.
    resp = await client.post(
        f"/chats/{env['chat'].id}/pending-input/call_q1",
        json={"answer": "us"},
        headers=auth_headers(env["user"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_requires_chat_ownership(client, committed_user_chat, db):
    env = committed_user_chat
    await _suspend_on_question(env)

    async with AsyncSessionLocal() as s:
        stranger = User(email=f"stranger-{uuid.uuid4().hex[:8]}@example.com")
        s.add(stranger)
        await s.flush()
        role = Role(name=f"stranger-role-{uuid.uuid4().hex[:8]}", description="t")
        s.add(role)
        await s.flush()
        s.add(RolePermission(role_id=role.id, permission_key="sinas.*:all", permission_value=True))
        s.add(UserRole(role_id=role.id, user_id=stranger.id, active=True))
        await s.commit()
        await s.refresh(stranger)
        stranger_id, role_id = stranger.id, role.id

    try:
        resp = await client.post(
            f"/chats/{env['chat'].id}/pending-input/call_q1",
            json={"answer": "hijacked"},
            headers=auth_headers(stranger),
        )
        assert resp.status_code == 403
        async with AsyncSessionLocal() as s:
            row = (
                await s.execute(
                    select(PendingCompletion).where(
                        PendingCompletion.chat_id == env["chat"].id
                    )
                )
            ).scalar_one()
            assert "call_q1" in row.pending  # untouched
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(UserRole).where(UserRole.user_id == stranger_id))
            await s.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
            await s.execute(delete(Role).where(Role.id == role_id))
            await s.execute(delete(User).where(User.id == stranger_id))
            await s.commit()


# ---------------------------------------------------------------------------
# Wiring: sweep and suspension signaling
# ---------------------------------------------------------------------------


def test_expiry_sweep_is_registered_on_the_agent_worker():
    from app.queue.worker import AgentWorkerSettings, sweep_deferred_expiry_job

    assert any(
        cj.coroutine is sweep_deferred_expiry_job for cj in AgentWorkerSettings.cron_jobs
    )


def test_agent_jobs_treat_round_suspension_as_terminal_handoff():
    from app.queue.agent_jobs import _is_suspension_chunk

    assert _is_suspension_chunk({"type": dc.ROUND_SUSPENDED})
    assert _is_suspension_chunk({"type": "delegation_pending"})
    assert not _is_suspension_chunk({"type": "tool_end"})
