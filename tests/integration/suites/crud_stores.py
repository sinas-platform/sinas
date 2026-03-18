"""CRUD tests for state stores."""

_NS = "test_integration"
_NAME = "test_store"


def teardown(ctx):
    try:
        ctx.client.delete(f"/api/v1/stores/{_NS}/{_NAME}", headers=ctx.admin_headers())
    except Exception:
        pass


def test_01_create_store(ctx):
    r = ctx.client.post(
        "/api/v1/stores",
        headers=ctx.admin_headers(),
        json={
            "namespace": _NS,
            "name": _NAME,
            "description": "Integration test store",
        },
    )
    assert r.status_code in (200, 201), f"Create failed: {r.text}"


def test_02_list_includes_created(ctx):
    r = ctx.client.get("/api/v1/stores", headers=ctx.admin_headers())
    assert r.status_code == 200
    stores = r.json()
    names = [f"{s['namespace']}/{s['name']}" for s in stores]
    assert f"{_NS}/{_NAME}" in names


def test_03_get_by_namespace_name(ctx):
    r = ctx.client.get(f"/api/v1/stores/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 200


def test_04_runtime_set_state(ctx):
    """Test writing state via runtime store API."""
    r = ctx.client.post(
        f"/stores/{_NS}/{_NAME}/states",
        headers=ctx.admin_headers(),
        json={"key": "test_key", "value": {"hello": "world"}},
    )
    assert r.status_code == 200, f"Set state failed: {r.text}"


def test_05_runtime_get_state(ctx):
    """Test reading state via runtime store API."""
    r = ctx.client.get(f"/stores/{_NS}/{_NAME}/states/test_key", headers=ctx.admin_headers())
    assert r.status_code == 200
    data = r.json()
    assert data["value"] == {"hello": "world"}


def test_06_runtime_delete_state(ctx):
    r = ctx.client.delete(f"/stores/{_NS}/{_NAME}/states/test_key", headers=ctx.admin_headers())
    assert r.status_code == 200

    r = ctx.client.get(f"/stores/{_NS}/{_NAME}/states/test_key", headers=ctx.admin_headers())
    assert r.status_code == 404


def test_07_delete_store(ctx):
    r = ctx.client.delete(f"/api/v1/stores/{_NS}/{_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 204
