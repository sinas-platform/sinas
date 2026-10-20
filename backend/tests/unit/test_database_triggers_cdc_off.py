"""With CDC_ENABLED=false, database triggers are kept but can't be switched on (#163).

Nothing polls with CDC off, so a trigger created or switched on then would
never fire: that reads as a data bug, not a disabled feature. The API refuses
both, explicitly. Stored triggers are never rewritten, so turning CDC back on
resumes exactly the ones that were on.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.database_connection import DatabaseConnection
from app.models.function import Function
from tests.conftest import auth_headers


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    """Trigger writes announce themselves to the CDC worker; swallow that."""

    class _Redis:
        def __getattr__(self, _name):
            async def _noop(*args, **kwargs):
                return None

            return _noop

    async def fake_get_redis():
        return _Redis()

    monkeypatch.setattr("app.core.redis.get_redis", fake_get_redis)


@pytest_asyncio.fixture
async def body(db: AsyncSession, admin_user) -> dict:
    conn = DatabaseConnection(
        name=f"warehouse-{uuid.uuid4().hex[:8]}", connection_type="postgresql",
        host="db", port=5432, database="dwh", username="reader",
    )
    fn = Function(
        user_id=admin_user.id, namespace=f"ns{uuid.uuid4().hex[:8]}", name="nightly",
        code="def handler(input, context):\n    return {}", input_schema={}, output_schema={},
    )
    db.add_all([conn, fn])
    await db.flush()
    return {
        "name": f"t{uuid.uuid4().hex[:6]}", "database_connection_id": str(conn.id),
        "table_name": "orders", "function_namespace": fn.namespace,
        "function_name": fn.name, "poll_column": "updated_at",
    }


async def _create(client, admin_user, body) -> None:
    r = await client.post("/api/v1/database-triggers", json=body, headers=auth_headers(admin_user))
    assert r.status_code == 201, r.text


async def test_info_reports_the_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "cdc_enabled", False)
    assert (await client.get("/info")).json()["features"]["cdc"] is False
    monkeypatch.setattr(settings, "cdc_enabled", True)
    assert (await client.get("/info")).json()["features"]["cdc"] is True


async def test_creating_is_refused(client, admin_user, body, monkeypatch):
    monkeypatch.setattr(settings, "cdc_enabled", False)
    h = auth_headers(admin_user)
    r = await client.post("/api/v1/database-triggers", json=body, headers=h)
    assert r.status_code == 403
    assert "CDC_ENABLED=false" in r.json()["detail"]
    assert (await client.get(f"/api/v1/database-triggers/{body['name']}", headers=h)).status_code == 404


async def test_switching_on_is_refused_but_pausing_and_editing_work(
    client, admin_user, body, monkeypatch
):
    await _create(client, admin_user, body)
    monkeypatch.setattr(settings, "cdc_enabled", False)
    h = auth_headers(admin_user)
    url = f"/api/v1/database-triggers/{body['name']}"

    r = await client.patch(url, json={"batch_size": 5}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.patch(url, json={"is_active": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False

    r = await client.patch(url, json={"is_active": True}, headers=h)
    assert r.status_code == 403
    assert "CDC_ENABLED=false" in r.json()["detail"]
    assert (await client.get(url, headers=h)).json()["is_active"] is False

    assert (await client.delete(url, headers=h)).status_code == 204


async def test_edits_that_repeat_is_active_true_still_work(client, admin_user, body, monkeypatch):
    """Clients that PATCH the whole object send is_active=true with every edit
    of a trigger that is on; that switches nothing on, so it isn't refused."""
    await _create(client, admin_user, body)
    monkeypatch.setattr(settings, "cdc_enabled", False)
    r = await client.patch(
        f"/api/v1/database-triggers/{body['name']}",
        json={"is_active": True, "batch_size": 7},
        headers=auth_headers(admin_user),
    )
    assert r.status_code == 200, r.text
    assert r.json()["batch_size"] == 7 and r.json()["is_active"] is True


async def test_a_missing_trigger_is_a_404_even_with_cdc_off(client, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "cdc_enabled", False)
    r = await client.patch(
        "/api/v1/database-triggers/does-not-exist", json={"is_active": True},
        headers=auth_headers(admin_user),
    )
    assert r.status_code == 404


async def test_turning_cdc_off_and_on_leaves_triggers_as_they_were(
    client, admin_user, body, monkeypatch
):
    await _create(client, admin_user, body)
    paused = {**body, "name": body["name"] + "p"}
    await _create(client, admin_user, paused)
    h = auth_headers(admin_user)
    await client.patch(f"/api/v1/database-triggers/{paused['name']}", json={"is_active": False}, headers=h)

    def states():
        return client.get("/api/v1/database-triggers", headers=h)

    async def by_name():
        rows = (await states()).json()
        return {t["name"]: t["is_active"] for t in rows if t["name"] in (body["name"], paused["name"])}

    before = await by_name()
    assert before == {body["name"]: True, paused["name"]: False}

    monkeypatch.setattr(settings, "cdc_enabled", False)
    assert await by_name() == before  # listed as stored, not rewritten
    monkeypatch.setattr(settings, "cdc_enabled", True)
    assert await by_name() == before


async def test_cdc_service_does_not_poll_when_off(monkeypatch):
    from app.cdc import service

    monkeypatch.setattr(settings, "cdc_enabled", False)

    class _NoManager:
        def __init__(self):
            raise AssertionError("CDC manager started with CDC off")

    monkeypatch.setattr(service, "CDCManager", _NoManager)
    stop = asyncio.Event()
    task = asyncio.create_task(service.run(stop))
    await asyncio.sleep(0)
    assert not task.done()  # idles instead of exiting (no restart loop)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_with_cdc_on_creating_works(client, admin_user, body, monkeypatch):
    monkeypatch.setattr(settings, "cdc_enabled", True)
    await _create(client, admin_user, body)
