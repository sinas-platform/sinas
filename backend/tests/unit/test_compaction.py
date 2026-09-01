"""Compaction: summarize-and-continue instead of the 100-message cliff.

Covers: the incremental refresh (each message summarized at most once, the
prior summary folded into the next), the injection of the stored summary
into the built history when windowing applies, and the schedule trigger
when the summary lags the window.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat import Chat, Message
from app.models.user import User
from app.services import compaction
from app.services.conversation_history import build_conversation_history
from app.services.skill_tools import SkillToolConverter


@pytest_asyncio.fixture
async def long_chat(db: AsyncSession, test_user: User) -> Chat:
    chat = Chat(user_id=test_user.id, title="long chat")
    db.add(chat)
    await db.flush()
    for i in range(10):
        role = "user" if i % 2 == 0 else "assistant"
        db.add(Message(chat_id=chat.id, role=role, content=f"message {i}"))
        await db.flush()  # distinct created_at ordering
    await db.refresh(chat)
    return chat


class TestRunCompaction:
    @pytest.mark.asyncio
    async def test_incremental_refresh(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 6)

        seen: list[tuple[str, int]] = []

        async def fake_summarize(_db, _chat, prior_summary, delta):
            seen.append((prior_summary, len(delta)))
            return f"summary covering {len(delta)} more"

        monkeypatch.setattr(compaction, "_summarize", fake_summarize)

        # First run: 10 messages, window 6 → summarize the first 4.
        result = await compaction.run_compaction(str(long_chat.id), db=db)
        assert result["covered_count"] == 4
        assert seen == [("", 4)]

        # No growth → no re-summarization.
        again = await compaction.run_compaction(str(long_chat.id), db=db)
        assert again["covered_count"] == 4
        assert len(seen) == 1

        # Chat grows by 3 → only the 3-message delta is folded in, with the
        # prior summary as input.
        for i in range(3):
            db.add(Message(chat_id=long_chat.id, role="user", content=f"more {i}"))
            await db.flush()
        result = await compaction.run_compaction(str(long_chat.id), db=db)
        assert result["covered_count"] == 7
        assert seen[-1] == ("summary covering 4 more", 3)

    @pytest.mark.asyncio
    async def test_short_chat_is_untouched(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 100)
        result = await compaction.run_compaction(str(long_chat.id), db=db)
        assert result is None
        assert compaction.get_compaction(long_chat) is None

    @pytest.mark.asyncio
    async def test_failed_summarize_keeps_previous_state(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 6)

        async def broken(_db, _chat, prior, delta):
            return None

        monkeypatch.setattr(compaction, "_summarize", broken)
        result = await compaction.run_compaction(str(long_chat.id), db=db)
        assert result is None
        assert compaction.get_compaction(long_chat) is None


class TestHistoryInjection:
    @pytest.mark.asyncio
    async def test_summary_injected_when_windowing(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 6)
        long_chat.chat_metadata = {
            "compaction": {"summary": "- user likes terse answers", "covered_count": 4}
        }
        await db.flush()

        scheduled: list[str] = []
        monkeypatch.setattr(
            compaction, "maybe_schedule_compaction", lambda chat_id: scheduled.append(chat_id)
        )

        messages = await build_conversation_history(db, long_chat, SkillToolConverter())
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert any("user likes terse answers" in m["content"] for m in system_msgs)
        # summary covers exactly the dropped prefix — no refresh needed
        assert scheduled == []
        # windowed history follows the summary
        assert sum(1 for m in messages if m["role"] in ("user", "assistant")) == 6

    @pytest.mark.asyncio
    async def test_lagging_summary_triggers_refresh(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 4)  # dropped = 6 > covered 4
        long_chat.chat_metadata = {
            "compaction": {"summary": "- old summary", "covered_count": 4}
        }
        await db.flush()

        scheduled: list[str] = []
        monkeypatch.setattr(
            compaction, "maybe_schedule_compaction", lambda chat_id: scheduled.append(chat_id)
        )
        await build_conversation_history(db, long_chat, SkillToolConverter())
        assert scheduled == [str(long_chat.id)]

    @pytest.mark.asyncio
    async def test_no_summary_no_injection_but_scheduled(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 6)
        scheduled: list[str] = []
        monkeypatch.setattr(
            compaction, "maybe_schedule_compaction", lambda chat_id: scheduled.append(chat_id)
        )
        messages = await build_conversation_history(db, long_chat, SkillToolConverter())
        assert not any("summarized" in (m.get("content") or "") for m in messages if m["role"] == "system")
        assert scheduled == [str(long_chat.id)]

    @pytest.mark.asyncio
    async def test_compaction_disabled_restores_pure_windowing(self, db, long_chat, monkeypatch):
        monkeypatch.setattr(settings, "max_history_messages", 6)
        monkeypatch.setattr(settings, "compaction_enabled", False)
        long_chat.chat_metadata = {
            "compaction": {"summary": "- should not appear", "covered_count": 4}
        }
        await db.flush()
        messages = await build_conversation_history(db, long_chat, SkillToolConverter())
        assert not any(
            "should not appear" in (m.get("content") or "") for m in messages
        )
