"""What a rendered component's code can reach.

It used to get the viewer's own access token (the console handed it to any
frame that asked), ran in the console's origin where they share one, and its
input was spliced unescaped into a <script>. Now it runs sandboxed with a
token scoped to the component: the viewer's live permissions capped to what
the component declares, on the component's routes only.
"""

import re
import time
import types
import uuid

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.component import Component
from app.services.component_access import (
    SESSION_MAX_SECONDS,
    component_token_claims,
    generate_component_access_token,
    scoped_permissions,
)
from app.services.content_tokens import generate_component_render_token
from tests.conftest import auth_headers


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def component(db: AsyncSession, admin_user) -> Component:
    comp = Component(
        user_id=admin_user.id, namespace=f"ui{_uid()}", name="board", title="Board <b>",
        source_code="export default () => null;", compiled_bundle="var __SinasComponent__={};",
        compile_status="success", enabled_queries=["sales/totals"],
        enabled_stores=[{"store": "sales/notes", "access": "readonly"}],
    )
    db.add(comp)
    await db.flush()
    return comp


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _render(client, component, user, **params):
    token = generate_component_render_token(component.namespace, component.name, str(user.id))
    return await client.get(
        f"/components/{component.namespace}/{component.name}/render",
        params={"token": token, **params},
    )


class TestScopedPermissions:
    def test_capped_to_the_declared_resources(self):
        comp = types.SimpleNamespace(
            enabled_queries=["sales/totals"], enabled_functions=[], enabled_agents=["help/bot"],
            enabled_stores=[
                {"store": "sales/notes", "access": "readonly"},
                {"store": "sales/drafts", "access": "readwrite"},
            ],
        )
        perms = scoped_permissions(comp, {"sinas.*:all": True})
        assert "sinas.queries/sales/totals.execute:own" in perms
        assert "sinas.agents/help/bot.chat:all" in perms
        assert "sinas.stores/sales/notes.read_state:own" in perms
        assert "sinas.stores/sales/notes.write_state:own" not in perms  # readonly
        assert "sinas.stores/sales/drafts.write_state:own" in perms
        assert not any(key.startswith("sinas.functions") for key in perms)

    def test_never_more_than_the_viewer_holds(self):
        comp = types.SimpleNamespace(
            enabled_queries=["sales/totals"], enabled_functions=["ops/purge"],
            enabled_agents=[], enabled_stores=[],
        )
        perms = scoped_permissions(comp, {"sinas.queries/*/*.execute:own": True})
        assert set(perms) == {"sinas.queries/sales/totals.execute:own"}


class TestRender:
    async def test_runs_sandboxed_with_a_component_token(self, client, component, admin_user):
        response = await _render(client, component, admin_user)
        assert response.status_code == 200
        assert response.headers["content-security-policy"].startswith("sandbox allow-scripts")
        assert "allow-same-origin" not in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        token = re.search(r"__SINAS_AUTH_TOKEN__ = \"([^\"]+)\"", response.text).group(1)
        claims = component_token_claims(token)
        assert (claims["namespace"], claims["name"], claims["sub"]) == (
            component.namespace, component.name, str(admin_user.id)
        )

    async def test_input_cannot_break_out_of_the_script(self, client, component, admin_user):
        payload = '{"q": "</script><script>alert(1)</script>"}'
        response = await _render(client, component, admin_user, input=payload)
        assert response.status_code == 200
        assert "</script><script>alert(1)" not in response.text
        assert "\\u003c/script\\u003e" in response.text

    async def test_the_title_is_escaped(self, client, component, admin_user):
        response = await _render(client, component, admin_user)
        assert "<title>Board &lt;b&gt;</title>" in response.text


class TestWhereTheTokenWorks:
    async def test_not_on_the_general_api(self, client, component, admin_user):
        token = generate_component_access_token(str(admin_user.id), component.namespace, component.name)
        response = await client.get("/api/v1/components", headers=_bearer(token))
        assert response.status_code == 403
        assert response.json()["detail"] == "A component token can't be used here"

    async def test_not_on_another_components_routes(self, client, component, admin_user):
        token = generate_component_access_token(str(admin_user.id), component.namespace, component.name)
        response = await client.post(
            f"/components/{component.namespace}/other/proxy/queries/sales/totals/execute",
            json={"input": {}}, headers=_bearer(token),
        )
        assert response.status_code == 403

    async def test_a_query_it_does_not_declare_is_refused(self, client, component, admin_user):
        token = generate_component_access_token(str(admin_user.id), component.namespace, component.name)
        response = await client.post(
            f"/components/{component.namespace}/{component.name}/proxy/queries/hr/salaries/execute",
            json={"input": {}}, headers=_bearer(token),
        )
        assert response.status_code == 403

    async def test_a_chat_with_an_undeclared_agent_is_refused(self, client, component, admin_user):
        """Chat routes accept the token; the agent chat permission is only in
        it for the component's enabled agents."""
        token = generate_component_access_token(str(admin_user.id), component.namespace, component.name)
        response = await client.post("/agents/default/anything/chats", json={}, headers=_bearer(token))
        assert response.status_code in (403, 404)
        assert response.json()["detail"] != "A component token can't be used here"

    async def test_purpose_tokens_are_not_access_tokens(self, client, component, admin_user):
        render = generate_component_render_token(component.namespace, component.name, str(admin_user.id))
        response = await client.get("/api/v1/components", headers=_bearer(render))
        assert response.status_code == 401


class TestRenewal:
    async def test_renews_within_the_session(self, client, component, admin_user):
        token = generate_component_access_token(str(admin_user.id), component.namespace, component.name)
        response = await client.post(
            f"/components/{component.namespace}/{component.name}/access-token", headers=_bearer(token)
        )
        assert response.status_code == 200, response.text
        renewed = component_token_claims(response.json()["token"])
        assert renewed["session_start"] == component_token_claims(token)["session_start"]

    async def test_not_past_a_working_day(self, client, component, admin_user):
        token = generate_component_access_token(
            str(admin_user.id), component.namespace, component.name,
            session_start=int(time.time()) - SESSION_MAX_SECONDS - 60,
        )
        response = await client.post(
            f"/components/{component.namespace}/{component.name}/access-token", headers=_bearer(token)
        )
        assert response.status_code == 401

    async def test_a_user_token_does_not_mint_component_tokens(self, client, component, admin_user):
        response = await client.post(
            f"/components/{component.namespace}/{component.name}/access-token",
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 403

    def test_an_expired_token_is_not_a_component_token(self, admin_user):
        expired = jwt.encode(
            {"sub": str(admin_user.id), "namespace": "a", "name": "b", "purpose": "component_access",
             "session_start": int(time.time()), "exp": int(time.time()) - 1},
            settings.secret_key, algorithm="HS256",
        )
        assert component_token_claims(expired) is None
