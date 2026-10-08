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
