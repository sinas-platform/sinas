"""Steering triad: per-chat lock, cooperative interrupt (#142), injection.

The invariants under test:
- exactly one agent loop runs per chat (second send injects, never races);
- an interrupt stops the loop at a tool-round boundary, leaves the
  transcript intact plus a system marker, and a fresh send clears a stale
  flag;
- a message persisted mid-turn is what injection *is* — the loop rebuilds
  its conversation from the DB at every round boundary.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat, Message
from app.models.user import User
from app.services import chat_steering
from app.services.message_service import MessageService


@pytest_asyncio.fixture
async def chat(db: AsyncSession, test_user: User) -> Chat:
    c = Chat(user_id=test_user.id, title="steering test chat")
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


class TestChatLock:
    @pytest.mark.asyncio
    async def test_lock_is_exclusive_and_releasable(self):
        chat_id = f"lock-test-{uuid.uuid4().hex}"
        token = await chat_steering.acquire_chat_lock(chat_id)
        assert token
        assert await chat_steering.is_chat_locked(chat_id)
        assert await chat_steering.acquire_chat_lock(chat_id) is None

        await chat_steering.release_chat_lock(chat_id, token)
        assert not await chat_steering.is_chat_locked(chat_id)
        token2 = await chat_steering.acquire_chat_lock(chat_id)
        assert token2

        # A stale holder must not release the new holder's lock.
        await chat_steering.release_chat_lock(chat_id, token)
        assert await chat_steering.is_chat_locked(chat_id)
        await chat_steering.release_chat_lock(chat_id, token2)

    @pytest.mark.asyncio
    async def test_acquire_wait_times_out_while_held(self):
        chat_id = f"lock-wait-{uuid.uuid4().hex}"
        token = await chat_steering.acquire_chat_lock(chat_id)
        assert await chat_steering.acquire_chat_lock_wait(chat_id, wait_seconds=1) is None
        await chat_steering.release_chat_lock(chat_id, token)
        token2 = await chat_steering.acquire_chat_lock_wait(chat_id, wait_seconds=1)
        assert token2
        await chat_steering.release_chat_lock(chat_id, token2)


class TestInterruptFlag:
    @pytest.mark.asyncio
    async def test_request_consume_clear(self):
        chat_id = f"intr-{uuid.uuid4().hex}"
        assert not await chat_steering.consume_interrupt(chat_id)
        await chat_steering.request_interrupt(chat_id, "tester")
        assert await chat_steering.consume_interrupt(chat_id)
        # consumed — gone
        assert not await chat_steering.consume_interrupt(chat_id)

        await chat_steering.request_interrupt(chat_id)
        await chat_steering.clear_interrupt(chat_id)
        assert not await chat_steering.consume_interrupt(chat_id)


class TestInjection:
    @pytest.mark.asyncio
    async def test_send_while_locked_injects_user_message(self, db, chat, test_user):
        """A send against a locked chat persists the message for the running
        loop to drain instead of starting a second loop."""
        token = await chat_steering.acquire_chat_lock(str(chat.id))
        try:
            service = MessageService(db)
            result = await service.send_message(
                chat_id=str(chat.id),
                user_id=str(test_user.id),
                user_token="",
                content="steer left!",
            )
            assert result.role == "user"
            assert result.content == "steer left!"

            rows = (
                await db.execute(select(Message).where(Message.chat_id == chat.id))
            ).scalars().all()
            assert [m.role for m in rows] == ["user"]
        finally:
            await chat_steering.release_chat_lock(str(chat.id), token)

    @pytest.mark.asyncio
    async def test_stream_while_locked_yields_injected_event(self, db, chat, test_user):
        token = await chat_steering.acquire_chat_lock(str(chat.id))
        try:
            service = MessageService(db)
            events = []
            async for chunk in service.send_message_stream(
                chat_id=str(chat.id),
                user_id=str(test_user.id),
                user_token="",
                content="mid-turn note",
            ):
                events.append(chunk)
            types = [e.get("type") for e in events]
            assert types == ["injected", "done"]
        finally:
            await chat_steering.release_chat_lock(str(chat.id), token)

    @pytest.mark.asyncio
    async def test_injection_into_other_users_chat_fails(self, db, chat, admin_user):
        token = await chat_steering.acquire_chat_lock(str(chat.id))
        try:
            service = MessageService(db)
            with pytest.raises(ValueError):
                await service.send_message(
                    chat_id=str(chat.id),
                    user_id=str(admin_user.id),
                    user_token="",
                    content="not my chat",
                )
        finally:
            await chat_steering.release_chat_lock(str(chat.id), token)


class TestInterruptBoundary:
    @pytest.mark.asyncio
    async def test_followup_boundary_stops_and_marks(self, db, chat, test_user):
        """An armed interrupt makes the tool-round boundary stop the loop and
        write the system marker — transcript intact."""
        db.add(Message(chat_id=chat.id, role="user", content="do things"))
        await db.commit()

        await chat_steering.request_interrupt(str(chat.id), "tester")
        service = MessageService(db)
        events = []
        async for chunk in service._stream_followup_after_tools(
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            user_token="",
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=[],
        ):
            events.append(chunk)
        assert events == [
            {"type": "interrupted", "content": chat_steering.INTERRUPT_MARKER}
        ]

        rows = (
            await db.execute(
                select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
            )
        ).scalars().all()
        assert [m.role for m in rows] == ["user", "system"]
        assert rows[-1].content == chat_steering.INTERRUPT_MARKER
        # flag consumed — the next boundary would run normally
        assert not await chat_steering.consume_interrupt(str(chat.id))

    @pytest.mark.asyncio
    async def test_tool_round_boundary_writes_synthetic_results(self, db, chat, test_user):
        """Interrupting before a round's tools run leaves no dangling
        tool_calls: every call gets a synthetic 'interrupted' result."""
        await chat_steering.request_interrupt(str(chat.id), "tester")
        service = MessageService(db)
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "some_tool", "arguments": "{}"}},
            {"id": "call_2", "type": "function", "function": {"name": "other_tool", "arguments": "{}"}},
        ]
        events = []
        async for chunk in service._handle_tool_calls(
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            user_token="",
            messages=[],
            tool_calls=tool_calls,
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=[],
        ):
            events.append(chunk)
        assert events[-1]["type"] == "interrupted"

        rows = (
            await db.execute(
                select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
            )
        ).scalars().all()
        roles = [m.role for m in rows]
        # assistant (tool_calls) + one tool result per call + system marker
        assert roles == ["assistant", "tool", "tool", "system"]
        tool_rows = [m for m in rows if m.role == "tool"]
        assert {m.tool_call_id for m in tool_rows} == {"call_1", "call_2"}
        for m in tool_rows:
            assert "Interrupted" in m.content


class TestInterruptEndpoint:
    @pytest.mark.asyncio
    async def test_interrupt_endpoint_arms_flag(self, db, chat, client, test_user):
        from tests.conftest import auth_headers

        resp = await client.post(f"/chats/{chat.id}/interrupt", headers=auth_headers(test_user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["interrupted"] is True
        assert body["was_running"] is False
        assert await chat_steering.consume_interrupt(str(chat.id))

    @pytest.mark.asyncio
    async def test_interrupt_endpoint_reports_running(self, db, chat, client, test_user):
        from tests.conftest import auth_headers

        token = await chat_steering.acquire_chat_lock(str(chat.id))
        try:
            resp = await client.post(f"/chats/{chat.id}/interrupt", headers=auth_headers(test_user))
            assert resp.status_code == 200
            assert resp.json()["was_running"] is True
        finally:
            await chat_steering.release_chat_lock(str(chat.id), token)

    @pytest.mark.asyncio
    async def test_interrupt_other_users_chat_is_404(self, db, chat, client, admin_user):
        from tests.conftest import auth_headers

        resp = await client.post(f"/chats/{chat.id}/interrupt", headers=auth_headers(admin_user))
        assert resp.status_code == 404
