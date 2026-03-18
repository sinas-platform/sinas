"""System and admin endpoint tests."""


def test_01_health(ctx):
    r = ctx.client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_02_system_health(ctx):
    """Admin system health — queries Docker, host resources, queue stats."""
    r = ctx.client.get("/api/v1/system/health", headers=ctx.admin_headers())
    assert r.status_code == 200, f"System health failed: {r.text}"
    data = r.json()
    assert "warnings" in data
    assert "services" in data
    assert "host" in data


def test_03_queue_workers(ctx):
    r = ctx.client.get("/api/v1/queue/workers", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_04_llm_providers_list(ctx):
    r = ctx.client.get("/api/v1/llm-providers", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_05_users_list(ctx):
    r = ctx.client.get("/api/v1/users", headers=ctx.admin_headers())
    assert r.status_code == 200
    users = r.json()
    assert len(users) >= 1


def test_06_config_export(ctx):
    r = ctx.client.get("/api/v1/config/export", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert len(r.text) > 0


def test_07_discovery_agents(ctx):
    r = ctx.client.get("/agents", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_08_discovery_functions(ctx):
    r = ctx.client.get("/functions", headers=ctx.admin_headers())
    assert r.status_code == 200
    assert isinstance(r.json(), list)
