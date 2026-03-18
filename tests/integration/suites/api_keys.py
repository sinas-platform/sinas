"""API key management tests + creates scoped keys for other suites."""

_created_key_ids = []


def setup(ctx):
    """Create test API keys with different permission levels."""
    # Full-access test key
    r = ctx.client.post(
        "/api/v1/api-keys",
        headers=ctx.admin_headers(),
        json={
            "name": "test_full_access",
            "permissions": {"sinas.*:all": True},
        },
    )
    assert r.status_code == 201, f"Failed to create full-access key: {r.text}"
    data = r.json()
    ctx.test_api_keys["full"] = data["key"]
    _created_key_ids.append(data["id"])

    # Read-only test key
    r = ctx.client.post(
        "/api/v1/api-keys",
        headers=ctx.admin_headers(),
        json={
            "name": "test_readonly",
            "permissions": {
                "sinas.agents.read:all": True,
                "sinas.functions.read:all": True,
                "sinas.queries.read:all": True,
                "sinas.users.read:own": True,
            },
        },
    )
    assert r.status_code == 201, f"Failed to create readonly key: {r.text}"
    data = r.json()
    ctx.test_api_keys["readonly"] = data["key"]
    _created_key_ids.append(data["id"])

    # No-permissions key
    r = ctx.client.post(
        "/api/v1/api-keys",
        headers=ctx.admin_headers(),
        json={
            "name": "test_no_perms",
            "permissions": {},
        },
    )
    assert r.status_code == 201, f"Failed to create no-perms key: {r.text}"
    data = r.json()
    ctx.test_api_keys["noperms"] = data["key"]
    _created_key_ids.append(data["id"])


def teardown(ctx):
    """Clean up test API keys. Skipped — keys are needed by other suites."""
    pass


def test_01_list_api_keys(ctx):
    r = ctx.client.get("/api/v1/api-keys", headers=ctx.admin_headers())
    assert r.status_code == 200
    keys = r.json()
    names = [k["name"] for k in keys]
    assert "test_full_access" in names
    assert "test_readonly" in names


def test_02_full_access_key_can_read(ctx):
    r = ctx.client.get("/auth/me", headers=ctx.key_headers("full"))
    assert r.status_code == 200


def test_03_readonly_key_can_read(ctx):
    r = ctx.client.get("/api/v1/agents", headers=ctx.key_headers("readonly"))
    assert r.status_code == 200


def test_04_readonly_key_cannot_create(ctx):
    r = ctx.client.post(
        "/api/v1/agents",
        headers=ctx.key_headers("readonly"),
        json={"namespace": "test", "name": "should_fail", "description": "x"},
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}"


def test_05_noperms_key_cannot_create(ctx):
    """API key with no permissions should be denied write operations."""
    r = ctx.client.post(
        "/api/v1/agents",
        headers=ctx.key_headers("noperms"),
        json={"namespace": "test", "name": "should_fail", "description": "x"},
    )
    assert r.status_code == 403, f"Expected 403, got {r.status_code}"


def test_06_invalid_key_rejected(ctx):
    r = ctx.client.get("/auth/me", headers={"X-API-Key": "sk-bogus-key-12345678"})
    assert r.status_code == 401
