"""Share links: snapshot, signed-in viewer, and as-the-creator.

- snapshot: the link's inputs only; the page gets no token
- viewer: forwards to the console; the signed-in user opens it with their
  own permissions (capped to the component)
- creator: anyone with the link acts as its creator, capped to the
  component, read only unless the link allows writes — and only while the
  link lives
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.component import Component
from app.models.component_share import ComponentShare
from app.services.component_access import component_token_claims
from tests.conftest import auth_headers


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def component(db: AsyncSession, admin_user) -> Component:
    comp = Component(
        user_id=admin_user.id, namespace=f"ui{_uid()}", name="board", source_code="<p>board</p>",
        enabled_queries=["sales/totals"], enabled_functions=["ops/purge"],
        enabled_stores=[{"store": "sales/notes", "access": "readwrite"}],
    )
    db.add(comp)
    await db.flush()
    return comp


async def _share(client, component, user, **body) -> dict:
    r = await client.post(
        f"/api/v1/components/{component.namespace}/{component.name}/shares",
        json=body, headers=auth_headers(user),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _embedded_token(html: str):
    match = re.search(r'__SINAS_AUTH_TOKEN__ = ("[^"]+"|null)', html)
    value = match.group(1)
    return None if value == "null" else value.strip('"')


class TestModes:
    async def test_snapshot_is_static(self, client, component, admin_user):
        share = await _share(client, component, admin_user, input_data={"q": "x"})
        assert share["mode"] == "snapshot"
        r = await client.get(share["share_url"])
        assert r.status_code == 200
        assert _embedded_token(r.text) is None
        assert '"q": "x"' in r.text

    async def test_creator_acts_for_the_creator_read_only_by_default(self, client, component, admin_user):
        share = await _share(client, component, admin_user, mode="creator")
        assert share["allow_writes"] is False
        token = _embedded_token((await client.get(share["share_url"])).text)
        claims = component_token_claims(token)
        assert claims["sub"] == str(admin_user.id) and claims["share_id"] == share["id"]

        h = {"Authorization": f"Bearer {token}"}
        base = f"/components/{component.namespace}/{component.name}/proxy"
        # functions can change things: not on a read-only link
        r = await client.post(f"{base}/functions/ops/purge/execute", json={"input": {}}, headers=h)
        assert r.status_code == 403
        # store writes neither, reads yes (the store itself may not exist: 404, not 403)
        r = await client.post(f"{base}/states/sales/notes", json={"action": "set", "key": "k", "value": {}}, headers=h)
        assert r.status_code == 403
        r = await client.post(f"{base}/states/sales/notes", json={"action": "list"}, headers=h)
        assert r.status_code != 403

    async def test_a_revoked_link_stops_working_at_once(self, client, db, component, admin_user):
        share = await _share(client, component, admin_user, mode="creator")
        token = _embedded_token((await client.get(share["share_url"])).text)
        await client.delete(
            f"/api/v1/components/{component.namespace}/{component.name}/shares/{share['token']}",
            headers=auth_headers(admin_user),
        )
        r = await client.post(
            f"/components/{component.namespace}/{component.name}/access-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    async def test_an_expired_creator_link_stops_live_access(self, client, db, component, admin_user):
        share = await _share(client, component, admin_user, mode="creator")
        token = _embedded_token((await client.get(share["share_url"])).text)
        row = await db.get(ComponentShare, uuid.UUID(share["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.flush()
        r = await client.post(
            f"/components/{component.namespace}/{component.name}/access-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401

    async def test_viewer_links_open_in_the_console_for_the_signed_in_user(
        self, client, component, admin_user, test_user
    ):
        share = await _share(client, component, admin_user, mode="viewer", input_data={"q": 1})
        r = await client.get(share["share_url"], follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].endswith(f"/ui/shared/{share['token']}")

        r = await client.post(f"/components/shared/{share['token']}/open")
        assert r.status_code == 401  # needs a signed-in user
        r = await client.post(f"/components/shared/{share['token']}/open", headers=auth_headers(test_user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["namespace"], body["input"]) == (component.namespace, {"q": 1})
        assert body["render_token"]


class TestLinks:
    async def test_writes_only_on_creator_links(self, client, component, admin_user):
        r = await client.post(
            f"/api/v1/components/{component.namespace}/{component.name}/shares",
            json={"mode": "viewer", "allow_writes": True}, headers=auth_headers(admin_user),
        )
        assert r.status_code == 422

    async def test_max_views_is_enforced(self, client, component, admin_user):
        share = await _share(client, component, admin_user, max_views=1)
        assert (await client.get(share["share_url"])).status_code == 200
        assert (await client.get(share["share_url"])).status_code == 410

    async def test_listing_shows_mode_and_writes(self, client, component, admin_user):
        await _share(client, component, admin_user, mode="creator", allow_writes=True, label="board")
        r = await client.get(
            f"/api/v1/components/{component.namespace}/{component.name}/shares",
            headers=auth_headers(admin_user),
        )
        [link] = r.json()
        assert (link["mode"], link["allow_writes"], link["label"]) == ("creator", True, "board")

    async def test_others_links_are_not_listed(self, client, db, component, admin_user):
        """A link's token is a credential (a creator link acts as its
        creator): reading the component must not reveal other people's."""
        from app.models.user import Role, RolePermission, User, UserRole

        await _share(client, component, admin_user, mode="creator")
        role = Role(name=f"reader-{_uid()}", description="reads components")
        db.add(role)
        await db.flush()
        db.add(RolePermission(role_id=role.id, permission_key="sinas.components/*/*.read:all", permission_value=True))
        reader = User(email=f"reader-{_uid()}@example.com")
        db.add(reader)
        await db.flush()
        db.add(UserRole(role_id=role.id, user_id=reader.id, active=True))
        await db.flush()
        r = await client.get(
            f"/api/v1/components/{component.namespace}/{component.name}/shares",
            headers=auth_headers(reader),
        )
        assert r.status_code == 200, r.text
        assert r.json() == []

    async def test_viewer_links_redirect_to_the_configured_console(
        self, client, component, admin_user, monkeypatch
    ):
        from app.core.config import settings

        monkeypatch.setattr(settings, "console_url", "https://console.example.com:51245/ui/")
        share = await _share(client, component, admin_user, mode="viewer")
        r = await client.get(share["share_url"], follow_redirects=False)
        assert r.headers["location"] == f"https://console.example.com:51245/ui/shared/{share['token']}"

    async def test_an_api_key_cannot_mint_a_creator_link(self, client, db, component, admin_user):
        """A creator link acts with its creator's full live permissions; a
        key's are deliberately narrower."""
        from app.core.auth import create_api_key

        _, key = await create_api_key(
            db, admin_user, "narrow", {"sinas.components/*/*.update:all": True}
        )
        url = f"/api/v1/components/{component.namespace}/{component.name}/shares"
        r = await client.post(url, json={"mode": "creator"}, headers={"X-API-Key": key})
        assert r.status_code == 403
        r = await client.post(url, json={"mode": "snapshot"}, headers={"X-API-Key": key})
        assert r.status_code == 200, r.text


class TestApiKeysNeverActAsTheOwner:
    """A key's permissions are narrower than its owner's; nothing that acts
    with the owner's full permissions is handed to a key."""

    async def _key(self, db, owner, permissions):
        from app.core.auth import create_api_key

        _, key = await create_api_key(db, owner, f"k-{_uid()}", permissions)
        return {"X-API-Key": key}

    async def test_creator_links_are_not_listed_to_a_key(self, client, db, component, admin_user):
        await _share(client, component, admin_user, mode="creator")
        await _share(client, component, admin_user, mode="snapshot")
        h = await self._key(db, admin_user, {"sinas.components/*/*.read:all": True})
        r = await client.get(
            f"/api/v1/components/{component.namespace}/{component.name}/shares", headers=h
        )
        assert r.status_code == 200, r.text
        assert [link["mode"] for link in r.json()] == ["snapshot"]

    async def test_a_key_cannot_open_a_viewer_link(self, client, db, component, admin_user):
        share = await _share(client, component, admin_user, mode="viewer")
        h = await self._key(db, admin_user, {"sinas.components/*/*.read:all": True})
        r = await client.post(f"/components/shared/{share['token']}/open", headers=h)
        assert r.status_code == 403

    async def test_component_responses_carry_no_render_token_for_a_key(
        self, client, db, component, admin_user
    ):
        h = await self._key(db, admin_user, {"sinas.components/*/*.read:all": True})
        r = await client.get(f"/api/v1/components/{component.namespace}/{component.name}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["render_token"] is None
        r = await client.get(
            f"/api/v1/components/{component.namespace}/{component.name}", headers=auth_headers(admin_user)
        )
        assert r.json()["render_token"]
