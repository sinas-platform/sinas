"""With CODE_EXECUTION_ENABLED=false, functions are view-only (SIN-875).

The console only greyed out its "New function" button: the editor's URL and
the API still created and changed functions that could never run.
"""

import uuid

import pytest

from app.core.config import settings
from app.models.function import Function
from tests.conftest import auth_headers


@pytest.fixture
def code_execution_off(monkeypatch):
    monkeypatch.setattr(settings, "code_execution_enabled", False)


def _body(name: str) -> dict:
    return {
        "namespace": "default", "name": name, "code": "def handler(input, ctx):\n    return {}\n",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    }


async def test_creating_is_refused(client, admin_user, code_execution_off):
    r = await client.post("/api/v1/functions", json=_body(f"f{uuid.uuid4().hex[:6]}"), headers=auth_headers(admin_user))
    assert r.status_code == 403
    assert "CODE_EXECUTION_ENABLED=false" in r.json()["detail"]


async def test_viewing_and_deleting_still_work_but_changing_is_refused(
    client, db, admin_user, code_execution_off
):
    name = f"f{uuid.uuid4().hex[:6]}"
    body = _body(name)
    db.add(Function(user_id=admin_user.id, **body))
    await db.flush()
    h = auth_headers(admin_user)
    assert (await client.get(f"/api/v1/functions/default/{name}", headers=h)).status_code == 200
    r = await client.put(f"/api/v1/functions/default/{name}", json={"description": "x"}, headers=h)
    assert r.status_code == 403
    assert (await client.delete(f"/api/v1/functions/default/{name}", headers=h)).status_code == 204


async def test_with_code_execution_on_nothing_changes(client, admin_user, monkeypatch):
    monkeypatch.setattr(settings, "code_execution_enabled", True)
    r = await client.post("/api/v1/functions", json=_body(f"f{uuid.uuid4().hex[:6]}"), headers=auth_headers(admin_user))
    assert r.status_code == 201, r.text
