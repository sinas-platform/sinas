"""Agent runs started through an API key act with the key's permissions.

Agent tools check the caller's permissions. For a chat started with an API
key, that check used to load the key OWNER's role permissions, so a key
allowed only to chat with an agent drove every tool the agent has, with the
owner's full rights. Now the key is the caller: its tools, queued jobs and
the components it is shown are capped by the key's live permissions.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.auth import (
    bind_api_key,
    create_api_key,
    current_api_key_id,
    get_effective_permissions,
    reset_api_key,
)
from app.core.permissions import check_permission
from app.models.database_connection import DatabaseConnection
from app.models.query import Query
from app.services.query_tools import QueryToolConverter

CHAT_ONLY = {"sinas.agents/*/*.chat:all": True}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _key(db, user, permissions=CHAT_ONLY):
    api_key, plain = await create_api_key(db, user, f"k-{_uid()}", permissions)
    return api_key, plain


class _Bound:
    def __init__(self, api_key_id):
        self.api_key_id = api_key_id

    def __enter__(self):
        self.token = bind_api_key(self.api_key_id)

    def __exit__(self, *exc):
        reset_api_key(self.token)


class TestEffectivePermissions:
    async def test_without_a_key_the_users_own(self, db, admin_user):
        perms = await get_effective_permissions(db, str(admin_user.id))
        assert check_permission(perms, "sinas.queries/sales/orders.execute:own")

    async def test_through_a_key_the_keys(self, db, admin_user):
        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            perms = await get_effective_permissions(db, str(admin_user.id))
        assert check_permission(perms, "sinas.agents/help/bot.chat:all")
        assert not check_permission(perms, "sinas.queries/sales/orders.execute:own")

    async def test_a_revoked_or_expired_key_grants_nothing(self, db, admin_user):
        api_key, _ = await _key(db, admin_user)
        api_key.is_active = False
        await db.flush()
        with _Bound(str(api_key.id)):
            assert await get_effective_permissions(db, str(admin_user.id)) == {}
        api_key.is_active = True
        api_key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.flush()
        with _Bound(str(api_key.id)):
            assert await get_effective_permissions(db, str(admin_user.id)) == {}

    async def test_another_users_key_grants_nothing(self, db, admin_user, test_user):
        api_key, _ = await _key(db, test_user)
        with _Bound(str(api_key.id)):
            assert await get_effective_permissions(db, str(admin_user.id)) == {}


class TestAgentTools:
    async def test_a_chat_only_key_cannot_run_the_agents_query(self, db, admin_user):
        conn = DatabaseConnection(
            name=f"wh-{_uid()}", connection_type="postgresql", host="localhost", port=5432,
            database="test", username="test",
        )
        db.add(conn)
        await db.flush()
        ns = f"sales{_uid()}"
        db.add(Query(
            user_id=admin_user.id, namespace=ns, name="orders", database_connection_id=conn.id,
            operation="read", sql="select 1",
        ))
        await db.flush()
        converter = QueryToolConverter()

        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            result = await converter.execute_query_tool(
                db, f"query_{ns}__orders", {}, str(admin_user.id), enabled_queries=[f"{ns}/orders"]
            )
        assert result.get("error") == "Permission denied"

        # The same tool for the owner's own session passes the check (and fails
        # later only because the test connection doesn't exist).
        result = await converter.execute_query_tool(
            db, f"query_{ns}__orders", {}, str(admin_user.id), enabled_queries=[f"{ns}/orders"]
        )
        assert result.get("error") != "Permission denied"


class TestCarriedIntoQueuedRuns:
    async def test_enqueued_agent_jobs_carry_the_key(self, db, admin_user, monkeypatch):
        from app.services import queue_service as qs

        captured = {}

        class _Pool:
            async def enqueue_job(self, name, **kwargs):
                captured.update(kwargs)

        class _Redis:
            async def set(self, *a, **k):
                return None

        async def pool():
            return _Pool()

        async def redis():
            return _Redis()

        monkeypatch.setattr(qs, "get_arq_pool", pool)
        monkeypatch.setattr(qs, "get_redis", redis)
        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            await qs.queue_service.enqueue_agent_message(
                chat_id="c", user_id=str(admin_user.id), user_token="", content="hi", channel_id="ch"
            )
        assert captured["api_key_id"] == str(api_key.id)

    async def test_the_job_runs_through_that_key(self):
        from app.queue import agent_jobs

        seen = []

        @agent_jobs._acting_as_job_key
        async def job(ctx, **kwargs):
            seen.append(current_api_key_id())

        await job({}, api_key_id="key-1")
        await job({})
        assert seen == ["key-1", None]
        assert current_api_key_id() is None  # nothing leaks past the job


class TestComponentsShownInAKeysChat:
    async def test_the_render_token_carries_the_key_and_the_page_is_capped(self, db, admin_user):
        from jose import jwt

        from app.core.config import settings
        from app.models.component import Component
        from app.services.component_access import scoped_permissions
        from app.services.content_tokens import generate_component_render_token

        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            token = generate_component_render_token("ui", "board", str(admin_user.id))
            capped = scoped_permissions(
                Component(enabled_queries=["sales/orders"], enabled_functions=[], enabled_agents=[], enabled_stores=[]),
                await get_effective_permissions(db, str(admin_user.id)),
            )
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        assert claims["api_key_id"] == str(api_key.id)
        assert capped == {}  # the key may chat, not run the component's query


class TestARequestOnAKeyActsThroughIt:
    async def test_the_handler_sees_the_key_bound_by_auth(self, client, db, admin_user):
        """The key is bound in the auth dependency; what the endpoint (and
        any agent run it starts) does must see it."""
        from fastapi import Depends

        from app.core.auth import get_current_user_with_permissions
        from app.main import app as fastapi_app

        async def acting(_=Depends(get_current_user_with_permissions)):
            return {"api_key_id": current_api_key_id()}

        fastapi_app.add_api_route("/__test/acting-key", acting, methods=["GET"])
        try:
            api_key, plain = await _key(db, admin_user)
            r = await client.get("/__test/acting-key", headers={"X-API-Key": plain})
            assert r.json() == {"api_key_id": str(api_key.id)}
            from tests.conftest import auth_headers

            r = await client.get("/__test/acting-key", headers=auth_headers(admin_user))
            assert r.json() == {"api_key_id": None}
        finally:
            fastapi_app.router.routes[:] = [
                route for route in fastapi_app.router.routes
                if getattr(route, "path", None) != "/__test/acting-key"
            ]


class TestStoresWithoutANamedStore:
    async def _setup(self, db, owner):
        from app.models.agent import Agent
        from app.models.state import State
        from app.models.store import Store

        ns = f"crm{_uid()}"
        enabled = Store(namespace=ns, name="notes", user_id=owner.id)
        other = Store(namespace=ns, name="secrets", user_id=owner.id)
        db.add_all([enabled, other])
        await db.flush()
        db.add_all([
            State(user_id=owner.id, store_id=enabled.id, key="visible", value={"v": 1}),
            State(user_id=owner.id, store_id=other.id, key="hidden", value={"v": 2}),
        ])
        agent = Agent(
            user_id=owner.id, namespace=ns, name="bot", system_prompt="x",
            enabled_stores=[{"store": f"{ns}/notes", "access": "readonly"}],
        )
        db.add(agent)
        await db.flush()
        return agent

    async def test_a_search_stays_in_the_agents_readable_stores(self, db, admin_user):
        from app.services.state_tools import StateTools

        agent = await self._setup(db, admin_user)
        result = await StateTools.execute_tool(
            db, "retrieve_state", {}, str(admin_user.id), agent_id=str(agent.id)
        )
        keys = {s["key"] for s in result.get("states", [])} if isinstance(result, dict) else set()
        assert "hidden" not in str(result)  # another store's state never reached
        assert "visible" in str(result) or keys == {"visible"}

    async def test_a_chat_only_key_reads_nothing(self, db, admin_user):
        from app.services.state_tools import StateTools

        agent = await self._setup(db, admin_user)
        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            result = await StateTools.execute_tool(
                db, "retrieve_state", {}, str(admin_user.id), agent_id=str(agent.id)
            )
        assert "visible" not in str(result) and "hidden" not in str(result)


class TestRefreshedTokensForAKeysReader:
    async def test_a_fresh_owner_token_is_capped_when_a_key_reads_it(self, db, admin_user):
        from jose import jwt

        from app.core.config import settings
        from app.services.content_tokens import (
            generate_component_render_token,
            refresh_component_render_tokens,
        )

        owner_token = generate_component_render_token("ui", "board", str(admin_user.id), api_key_id=None)
        part = {"type": "component", "namespace": "ui", "name": "board", "render_token": owner_token}
        assert refresh_component_render_tokens([part], str(admin_user.id))[0]["render_token"] == owner_token

        api_key, _ = await _key(db, admin_user)
        with _Bound(str(api_key.id)):
            [refreshed] = refresh_component_render_tokens([part], str(admin_user.id))
        claims = jwt.decode(refreshed["render_token"], settings.secret_key, algorithms=["HS256"])
        assert claims["api_key_id"] == str(api_key.id)
