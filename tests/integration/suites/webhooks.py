"""Webhook CRUD and trigger tests."""

_NS = "test_integration"
_FN_NAME = "webhook_handler"
_WH_PATH = "test_integration/test_hook"


def setup(ctx):
    """Create a function for the webhook to call."""
    # Clean up from previous runs
    ctx.client.delete(f"/api/v1/webhooks/{_WH_PATH}", headers=ctx.admin_headers())
    ctx.client.delete(f"/api/v1/functions/{_NS}/{_FN_NAME}", headers=ctx.admin_headers())
    ctx.client.post(
        "/api/v1/functions",
        headers=ctx.admin_headers(),
        json={
            "namespace": _NS,
            "name": _FN_NAME,
            "description": "Webhook handler for tests",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "code": "def handler(input, context):\n    return {'received': input}",
        },
    )


def teardown(ctx):
    try:
        ctx.client.delete(f"/api/v1/webhooks/{_WH_PATH}", headers=ctx.admin_headers())
    except Exception:
        pass
    try:
        ctx.client.delete(f"/api/v1/functions/{_NS}/{_FN_NAME}", headers=ctx.admin_headers())
    except Exception:
        pass


def test_01_create_webhook(ctx):
    r = ctx.client.post(
        "/api/v1/webhooks",
        headers=ctx.admin_headers(),
        json={
            "path": _WH_PATH,
            "http_method": "POST",
            "function_namespace": _NS,
            "function_name": _FN_NAME,
            "description": "Integration test webhook",
        },
    )
    assert r.status_code in (200, 201), f"Create failed: {r.text}"


def test_02_list_includes_created(ctx):
    r = ctx.client.get("/api/v1/webhooks", headers=ctx.admin_headers())
    assert r.status_code == 200
    paths = [w["path"] for w in r.json()]
    assert _WH_PATH in paths


def test_03_trigger_webhook(ctx):
    """Trigger the webhook and verify it accepted the request."""
    r = ctx.client.post(
        f"/webhooks/{_WH_PATH}",
        headers={"Authorization": f"Bearer {ctx.admin_key}"},
        json={"test": True},
    )
    # Webhooks return 200 with execution_id (async) or result
    assert r.status_code == 200, f"Trigger failed ({r.status_code}): {r.text}"


def test_04_delete_webhook(ctx):
    r = ctx.client.delete(f"/api/v1/webhooks/{_WH_PATH}", headers=ctx.admin_headers())
    assert r.status_code in (200, 204)
