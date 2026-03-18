"""Health and connectivity tests."""


def test_health_endpoint(ctx):
    r = ctx.client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"


def test_api_docs_accessible(ctx):
    r = ctx.client.get("/docs")
    assert r.status_code == 200


def test_management_docs_accessible(ctx):
    r = ctx.client.get("/api/v1/docs")
    assert r.status_code == 200


def test_admin_key_works(ctx):
    """Verify the admin API key can authenticate."""
    r = ctx.client.get("/auth/me", headers=ctx.admin_headers())
    assert r.status_code == 200, f"Admin key rejected: {r.text}"
    data = r.json()
    assert "email" in data
