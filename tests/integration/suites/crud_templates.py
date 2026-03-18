"""CRUD tests for templates."""

_NS = "test_integration"
_NAME = "test_template"
_template_id = None


def teardown(ctx):
    if _template_id:
        try:
            ctx.client.delete(f"/api/v1/templates/{_template_id}", headers=ctx.admin_headers())
        except Exception:
            pass


def test_01_create_template(ctx):
    global _template_id
    r = ctx.client.post(
        "/api/v1/templates",
        headers=ctx.admin_headers(),
        json={
            "namespace": _NS,
            "name": _NAME,
            "description": "Test template",
            "html_content": "<h1>Hello {{ name }}</h1>",
        },
    )
    assert r.status_code == 201, f"Create failed: {r.text}"
    _template_id = r.json()["id"]


def test_02_list_includes_created(ctx):
    r = ctx.client.get("/api/v1/templates", headers=ctx.admin_headers())
    assert r.status_code == 200
    templates = r.json()
    names = [f"{t['namespace']}/{t['name']}" for t in templates]
    assert f"{_NS}/{_NAME}" in names


def test_03_get_by_name(ctx):
    r = ctx.client.get(f"/api/v1/templates/by-name/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 200


def test_04_render_template(ctx):
    r = ctx.client.post(
        f"/templates/{_NS}/{_NAME}/render",
        headers=ctx.admin_headers(),
        json={"variables": {"name": "World"}},
    )
    assert r.status_code == 200, f"Render failed: {r.text}"
    data = r.json()
    assert "World" in data.get("html_content", "")


def test_05_delete_template(ctx):
    r = ctx.client.delete(f"/api/v1/templates/{_template_id}", headers=ctx.admin_headers())
    assert r.status_code == 204
