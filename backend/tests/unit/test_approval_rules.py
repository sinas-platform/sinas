"""Permission modes: tool approval rules + per-chat session grants.

Layered over requires_approval: first-match glob rules per agent decide
auto vs ask for ANY tool kind, an explicit 'auto' rule can deliberately
override a function's requires_approval, and an approval given with
always_allow=true is remembered for the rest of the chat.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Chat
from app.models.function import Function
from app.models.pending_approval import PendingToolApproval
from app.models.user import User
from app.services import approval_rules
from app.services.approval_rules import resolve_action
from app.services.tool_execution import check_approval_requirements


class TestResolveAction:
    def test_defaults_to_auto_with_no_config(self):
        assert resolve_action(None, "any_tool", intrinsic_ask=False, session_grants=set()) == "auto"

    def test_intrinsic_ask_without_rules(self):
        assert resolve_action(None, "risky_fn", intrinsic_ask=True, session_grants=set()) == "ask"

    def test_first_matching_rule_wins(self):
        cfg = {
            "rules": [
                {"match": "write_*", "action": "ask"},
                {"match": "write_file_docs", "action": "auto"},  # never reached
            ]
        }
        assert resolve_action(cfg, "write_file_docs", False, set()) == "ask"

    def test_glob_matching(self):
        cfg = {"rules": [{"match": "workbench_*", "action": "ask"}]}
        assert resolve_action(cfg, "workbench_promote", False, set()) == "ask"
        assert resolve_action(cfg, "search_collection_docs", False, set()) == "auto"

    def test_auto_rule_overrides_intrinsic_requires_approval(self):
        cfg = {"rules": [{"match": "get_*", "action": "auto"}]}
        assert resolve_action(cfg, "get_report", intrinsic_ask=True, session_grants=set()) == "auto"

    def test_default_ask_catches_unmatched(self):
        cfg = {"default": "ask", "rules": [{"match": "read_*", "action": "auto"}]}
        assert resolve_action(cfg, "read_thing", False, set()) == "auto"
        assert resolve_action(cfg, "delete_thing", False, set()) == "ask"

    def test_session_grant_beats_everything(self):
        cfg = {"default": "ask", "rules": [{"match": "*", "action": "ask"}]}
        assert resolve_action(cfg, "granted_tool", True, {"granted_tool"}) == "auto"

    def test_malformed_rules_are_skipped(self):
        cfg = {"rules": [{"match": None}, {"action": "ask"}, {"match": "x", "action": "maybe"}]}
        assert resolve_action(cfg, "x", False, set()) == "auto"

    def test_invalid_default_falls_back_to_auto(self):
        assert resolve_action({"default": "yolo"}, "x", False, set()) == "auto"


class TestSessionGrants:
    @pytest.mark.asyncio
    async def test_grant_roundtrip(self):
        chat_id = f"grants-{uuid.uuid4().hex}"
        assert await approval_rules.get_session_grants(chat_id) == set()
        await approval_rules.add_session_grant(chat_id, "workbench_promote")
        await approval_rules.add_session_grant(chat_id, "send_email")
        assert await approval_rules.get_session_grants(chat_id) == {
            "workbench_promote",
            "send_email",
        }


@pytest_asyncio.fixture
async def agent_with_rules(db: AsyncSession, test_user: User) -> Agent:
    agent = Agent(
        namespace="test",
        name=f"gated-{uuid.uuid4().hex[:6]}",
        user_id=test_user.id,
        tool_approvals={"rules": [{"match": "workbench_promote", "action": "ask"}]},
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return agent


@pytest_asyncio.fixture
async def gated_chat(db: AsyncSession, test_user: User, agent_with_rules: Agent) -> Chat:
    c = Chat(
        user_id=test_user.id,
        agent_id=agent_with_rules.id,
        agent_namespace=agent_with_rules.namespace,
        agent_name=agent_with_rules.name,
        title="gated chat",
    )
    db.add(c)
    await db.flush()
    await db.refresh(c)
    return c


def _call(tool_name: str, call_id: str | None = None) -> dict:
    return {
        "id": call_id or f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": tool_name, "arguments": "{}"},
    }


class TestCheckApprovalRequirements:
    @pytest.mark.asyncio
    async def test_rule_gates_generic_tool(self, db, gated_chat, test_user):
        """A rule can put ANY tool behind approval — not just functions."""
        import uuid as _uuid
        from app.models.chat import Message

        msg = Message(chat_id=gated_chat.id, role="assistant", content=None)
        db.add(msg)
        await db.flush()

        asked = await check_approval_requirements(
            db=db,
            tool_calls=[_call("workbench_promote", "call_gate1"), _call("workbench_read")],
            chat_id=str(gated_chat.id),
            user_id=str(test_user.id),
            message_id=str(msg.id),
            messages=[],
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=[],
        )
        assert [e["tool_call_id"] for e in asked] == ["call_gate1"]
        assert asked[0]["function_namespace"] == "tool"
        assert asked[0]["function_name"] == "workbench_promote"

        pending = (
            await db.execute(
                select(PendingToolApproval).where(PendingToolApproval.chat_id == gated_chat.id)
            )
        ).scalars().all()
        assert len(pending) == 1 and pending[0].tool_call_id == "call_gate1"

    @pytest.mark.asyncio
    async def test_session_grant_suppresses_ask(self, db, gated_chat, test_user):
        from app.models.chat import Message

        await approval_rules.add_session_grant(str(gated_chat.id), "workbench_promote")
        msg = Message(chat_id=gated_chat.id, role="assistant", content=None)
        db.add(msg)
        await db.flush()

        asked = await check_approval_requirements(
            db=db,
            tool_calls=[_call("workbench_promote")],
            chat_id=str(gated_chat.id),
            user_id=str(test_user.id),
            message_id=str(msg.id),
            messages=[],
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=[],
        )
        assert asked == []

    @pytest.mark.asyncio
    async def test_no_rules_keeps_generic_tools_auto(self, db, test_user):
        """Back-compat: without rules, only requires_approval functions ask."""
        from app.models.chat import Message

        chat = Chat(user_id=test_user.id, title="plain chat")
        db.add(chat)
        await db.flush()
        msg = Message(chat_id=chat.id, role="assistant", content=None)
        db.add(msg)
        await db.flush()

        asked = await check_approval_requirements(
            db=db,
            tool_calls=[_call("workbench_promote"), _call("anything_else")],
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            message_id=str(msg.id),
            messages=[],
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=[],
        )
        assert asked == []

    @pytest.mark.asyncio
    async def test_auto_rule_unlocks_requires_approval_function(self, db, test_user):
        from app.models.chat import Message

        fn = Function(
            namespace="test",
            name=f"guarded-{uuid.uuid4().hex[:6]}",
            user_id=test_user.id,
            code="def handler(i, c): return {}",
            input_schema={},
            output_schema={},
            requires_approval=True,
        )
        agent = Agent(
            namespace="test",
            name=f"trusting-{uuid.uuid4().hex[:6]}",
            user_id=test_user.id,
            tool_approvals={"rules": [{"match": "*", "action": "auto"}]},
        )
        db.add_all([fn, agent])
        await db.flush()
        chat = Chat(user_id=test_user.id, agent_id=agent.id, title="trusting chat")
        db.add(chat)
        await db.flush()
        msg = Message(chat_id=chat.id, role="assistant", content=None)
        db.add(msg)
        await db.flush()

        tool_name = f"{fn.namespace}_{fn.name}"
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "_metadata": {"namespace": fn.namespace, "name": fn.name},
                },
            }
        ]
        asked = await check_approval_requirements(
            db=db,
            tool_calls=[_call(tool_name)],
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            message_id=str(msg.id),
            messages=[],
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=tools,
        )
        assert asked == []

        # Without the agent rule the same function asks.
        agent.tool_approvals = None
        await db.flush()
        asked = await check_approval_requirements(
            db=db,
            tool_calls=[_call(tool_name)],
            chat_id=str(chat.id),
            user_id=str(test_user.id),
            message_id=str(msg.id),
            messages=[],
            provider=None,
            model=None,
            temperature=0.7,
            max_tokens=None,
            tools=tools,
        )
        assert len(asked) == 1
        assert asked[0]["function_namespace"] == fn.namespace
