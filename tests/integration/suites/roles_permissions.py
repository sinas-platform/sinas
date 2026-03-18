"""Roles and permissions tests."""

import uuid

_ROLE_NAME = f"test_role_{uuid.uuid4().hex[:8]}"
_role_id = None


def teardown(ctx):
    if _role_id:
        try:
            ctx.client.delete(f"/api/v1/roles/{_ROLE_NAME}", headers=ctx.admin_headers())
        except Exception:
            pass


def test_01_create_role(ctx):
    global _role_id
    r = ctx.client.post(
        "/api/v1/roles",
        headers=ctx.admin_headers(),
        json={"name": _ROLE_NAME, "description": "Integration test role"},
    )
    assert r.status_code in (200, 201), f"Create failed: {r.text}"
    _role_id = r.json()["id"]


def test_02_list_roles(ctx):
    r = ctx.client.get("/api/v1/roles", headers=ctx.admin_headers())
    assert r.status_code == 200
    names = [role["name"] for role in r.json()]
    assert _ROLE_NAME in names


def test_03_get_role(ctx):
    r = ctx.client.get(f"/api/v1/roles/{_ROLE_NAME}", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert r.json()["description"] == "Integration test role"


def test_04_set_permission(ctx):
    r = ctx.client.post(
        f"/api/v1/roles/{_ROLE_NAME}/permissions",
        headers=ctx.admin_headers(),
        json={"permission_key": "sinas.functions.read:all", "permission_value": True},
    )
    assert r.status_code == 200, f"Set permission failed: {r.text}"


def test_05_get_permissions(ctx):
    r = ctx.client.get(f"/api/v1/roles/{_ROLE_NAME}/permissions", headers=ctx.admin_headers())
    assert r.status_code == 200
    perms = r.json()
    keys = [p["permission_key"] for p in perms]
    assert "sinas.functions.read:all" in keys


def test_06_check_permissions_endpoint(ctx):
    """Test the check-permissions endpoint with admin key."""
    r = ctx.client.post(
        "/auth/check-permissions",
        headers=ctx.admin_headers(),
        json={
            "permissions": ["sinas.functions.read:all", "sinas.agents.create:own"],
            "logic": "AND",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["result"] is True  # Admin has all permissions


def test_07_check_permissions_or_logic(ctx):
    r = ctx.client.post(
        "/auth/check-permissions",
        headers=ctx.admin_headers(),
        json={
            "permissions": ["sinas.nonexistent.action:all", "sinas.functions.read:all"],
            "logic": "OR",
        },
    )
    assert r.status_code == 200
    assert r.json()["result"] is True  # At least one matches via wildcard


def test_08_permissions_reference(ctx):
    """Test the permissions reference endpoint."""
    r = ctx.client.get("/api/v1/roles/permissions/reference", headers=ctx.admin_headers())
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0


def test_09_delete_role(ctx):
    r = ctx.client.delete(f"/api/v1/roles/{_ROLE_NAME}", headers=ctx.admin_headers())
    assert r.status_code in (200, 204)
