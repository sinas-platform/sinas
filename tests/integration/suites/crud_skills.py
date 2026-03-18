"""CRUD tests for skills."""

_NS = "test_integration"
_NAME = "test_skill"


def teardown(ctx):
    try:
        ctx.client.delete(f"/api/v1/skills/{_NS}/{_NAME}", headers=ctx.admin_headers())
    except Exception:
        pass


def test_01_create_skill(ctx):
    r = ctx.client.post(
        "/api/v1/skills",
        headers=ctx.admin_headers(),
        json={
            "namespace": _NS,
            "name": _NAME,
            "description": "Integration test skill",
            "content": "Always be polite and helpful.",
        },
    )
    assert r.status_code in (200, 201), f"Create failed: {r.text}"
    data = r.json()
    assert data["namespace"] == _NS
    assert data["name"] == _NAME


def test_02_list_includes_created(ctx):
    r = ctx.client.get("/api/v1/skills", headers=ctx.admin_headers())
    assert r.status_code == 200
    skills = r.json()
    names = [f"{s['namespace']}/{s['name']}" for s in skills]
    assert f"{_NS}/{_NAME}" in names


def test_03_get_by_namespace_name(ctx):
    r = ctx.client.get(f"/api/v1/skills/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert r.json()["content"] == "Always be polite and helpful."


def test_04_update_skill(ctx):
    r = ctx.client.put(
        f"/api/v1/skills/{_NS}/{_NAME}",
        headers=ctx.admin_headers(),
        json={"content": "Updated content."},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "Updated content."


def test_05_delete_skill(ctx):
    r = ctx.client.delete(f"/api/v1/skills/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 204

    r = ctx.client.get(f"/api/v1/skills/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 404
