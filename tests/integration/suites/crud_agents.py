"""CRUD tests for agents."""
import uuid

_NS = "test_integration"
_NAME = f"agent_{uuid.uuid4().hex[:8]}"


def teardown(ctx):
    try:
        ctx.client.delete(f"/api/v1/agents/{_NS}/{_NAME}", headers=ctx.admin_headers())
    except Exception:
        pass


def test_01_create_agent(ctx):
    # Clean up from any previous failed run
    ctx.client.delete(f"/api/v1/agents/{_NS}/{_NAME}", headers=ctx.admin_headers())
    r = ctx.client.post(
        "/api/v1/agents",
        headers=ctx.admin_headers(),
        json={
            "namespace": _NS,
            "name": _NAME,
            "description": "Integration test agent",
            "system_prompt": "You are a test agent.",
        },
    )
    assert r.status_code == 201, f"Create failed: {r.text}"
    data = r.json()
    assert data["namespace"] == _NS
    assert data["name"] == _NAME


def test_02_list_includes_created(ctx):
    r = ctx.client.get("/api/v1/agents", headers=ctx.admin_headers())
    assert r.status_code == 200
    agents = r.json()
    names = [f"{a['namespace']}/{a['name']}" for a in agents]
    assert f"{_NS}/{_NAME}" in names


def test_03_get_by_namespace_name(ctx):
    r = ctx.client.get(f"/api/v1/agents/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert r.json()["description"] == "Integration test agent"


def test_04_update_agent(ctx):
    r = ctx.client.put(
        f"/api/v1/agents/{_NS}/{_NAME}",
        headers=ctx.admin_headers(),
        json={"description": "Updated agent"},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "Updated agent"


def test_05_duplicate_name_rejected(ctx):
    r = ctx.client.post(
        "/api/v1/agents",
        headers=ctx.admin_headers(),
        json={"namespace": _NS, "name": _NAME, "description": "dup"},
    )
    assert r.status_code == 400


def test_06_delete_agent(ctx):
    r = ctx.client.delete(f"/api/v1/agents/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 204

    # Agent is soft-deleted (is_active=false), GET still returns it
    r = ctx.client.get(f"/api/v1/agents/{_NS}/{_NAME}", headers=ctx.admin_headers())
    if r.status_code == 200:
        assert r.json()["is_active"] is False
    else:
        assert r.status_code == 404


def test_07_unauthenticated_rejected(ctx):
    r = ctx.client.get("/api/v1/agents")
    assert r.status_code in (401, 403)
