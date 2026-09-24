"""A resumed turn that dies must leave a trace in the chat (#132).

The interactive path persists an assistant error row before giving up. The
queue re-entry points — approval resume and delegate resume — stream outside
that handler, so a failure reached only the Redis relay and the job-status
key: the transcript stopped at the assistant tool-call row, indistinguishable
from a message-storage bug.

These cover the helper both handlers call, including its own failure: it must
never mask the error it is recording.
"""

import inspect
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.models.chat import Chat, Message
from app.models.user import User
from app.queue import agent_jobs
from app.queue.agent_jobs import _persist_turn_error


@pytest_asyncio.fixture(autouse=True)
async def _dispose_shared_engine():
    """The app's engine is module-level and binds to the loop that first uses
    it; each test runs in a fresh loop. These tests exercise code that opens
    its own session through that engine, so drop its pooled connections after
    each one (the same reason conftest resets the Redis client)."""
    yield
    from app.core.database import async_engine

    await async_engine.dispose()


@pytest_asyncio.fixture
async def live_chat():
    """A chat that really exists in the database.

    `_persist_turn_error` deliberately opens its own session: by the time a
    resume job's handler runs, the session it was streaming with is gone. So
    the rolled-back `db` fixture is invisible to it and setup has to commit
    for real — and clean up after itself.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as setup:
        user = User(email=f"resume-{uuid.uuid4().hex[:8]}@example.com")
        setup.add(user)
        await setup.flush()
        chat = Chat(user_id=user.id, title="a turn that will die")
        setup.add(chat)
        await setup.commit()
        chat_id, user_id = chat.id, user.id

    try:
        yield chat_id
    finally:
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(delete(Message).where(Message.chat_id == chat_id))
            await cleanup.execute(delete(Chat).where(Chat.id == chat_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()


async def _messages(chat_id) -> list[Message]:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(select(Message).where(Message.chat_id == chat_id))
            ).scalars()
        )


class TestPersistTurnError:
    async def test_writes_a_visible_assistant_row(self, live_chat):
        await _persist_turn_error(str(live_chat), RuntimeError("429 RESOURCE_EXHAUSTED"))

        rows = await _messages(live_chat)
        assert len(rows) == 1
        assert rows[0].role == "assistant"
        assert "An error occurred" in rows[0].content
        # The operator needs the cause, not just that something broke
        assert "RESOURCE_EXHAUSTED" in rows[0].content

    async def test_a_long_error_is_truncated_not_dropped(self, live_chat):
        await _persist_turn_error(str(live_chat), RuntimeError("x" * 5000))
        [row] = await _messages(live_chat)
        assert 0 < row.content.count("x") <= 300

    async def test_a_failed_write_never_masks_the_original_error(self):
        """No chat to write to — the helper must still return quietly, or it
        would replace the real failure in the handler's traceback."""
        await _persist_turn_error(str(uuid.uuid4()), RuntimeError("the real failure"))


class TestHandlersRecordFailures:
    @pytest.mark.parametrize(
        "job", ["execute_agent_resume_job", "execute_agent_delegate_resume_job"]
    )
    def test_both_resume_handlers_persist_before_relaying(self, job):
        """Guards the wiring: publishing to the relay alone is what left the
        transcript silent, and the relay message is not durable."""
        fn = getattr(agent_jobs, job, None)
        assert fn is not None, f"{job} not found — rename? update this test"
        source = inspect.getsource(fn)
        assert "_persist_turn_error" in source, (
            f"{job} relays failures but no longer records them in the chat"
        )
