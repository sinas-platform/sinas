"""Declarative config apply tests."""

_NS = "test_config_apply"

_TEST_CONFIG = """
apiVersion: sinas.co/v1
kind: SinasConfig
metadata:
  name: integration_test_config
spec:
  functions:
    - namespace: test_config_apply
      name: config_fn
      description: Created by config apply test
      code: |
        def handler(input, context):
            return {"status": "ok"}

  skills:
    - namespace: test_config_apply
      name: config_skill
      description: Created by config apply test
      content: "Be helpful."

  stores:
    - namespace: test_config_apply
      name: config_store
      description: Created by config apply test
"""


def teardown(ctx):
    for resource_type, ns, name in [
        ("functions", _NS, "config_fn"),
        ("skills", _NS, "config_skill"),
        ("stores", _NS, "config_store"),
    ]:
        try:
            ctx.client.delete(f"/api/v1/{resource_type}/{ns}/{name}", headers=ctx.admin_headers())
        except Exception:
            pass


def test_01_validate_config(ctx):
    r = ctx.client.post(
        "/api/v1/config/validate",
        headers=ctx.admin_headers(),
        json={"config": _TEST_CONFIG},
    )
    assert r.status_code == 200, f"Validate failed: {r.text}"


def test_02_dry_run(ctx):
    r = ctx.client.post(
        "/api/v1/config/apply",
        headers=ctx.admin_headers(),
        json={"config": _TEST_CONFIG, "dryRun": True},
    )
    assert r.status_code == 200, f"Dry run failed: {r.text}"
    data = r.json()
    summary = data.get("summary", {})
    # Summary has created/updated/unchanged/deleted dicts
    total_created = sum(summary.get("created", {}).values())
    assert total_created > 0, f"Expected created resources in dry run, got summary: {summary}"


def test_03_apply_config(ctx):
    r = ctx.client.post(
        "/api/v1/config/apply",
        headers=ctx.admin_headers(),
        json={"config": _TEST_CONFIG, "dryRun": False},
    )
    assert r.status_code == 200, f"Apply failed: {r.text}"


def test_04_resources_created(ctx):
    r = ctx.client.get(f"/api/v1/functions/{_NS}/config_fn", headers=ctx.admin_headers())
    assert r.status_code == 200, f"Function not created: {r.text}"

    r = ctx.client.get(f"/api/v1/skills/{_NS}/config_skill", headers=ctx.admin_headers())
    assert r.status_code == 200, "Skill not created"

    r = ctx.client.get(f"/api/v1/stores/{_NS}/config_store", headers=ctx.admin_headers())
    assert r.status_code == 200, "Store not created"


def test_05_idempotent_reapply(ctx):
    """Applying the same config again should result in no changes."""
    r = ctx.client.post(
        "/api/v1/config/apply",
        headers=ctx.admin_headers(),
        json={"config": _TEST_CONFIG, "dryRun": True},
    )
    assert r.status_code == 200
    data = r.json()
    summary = data.get("summary", {})
    total_created = sum(summary.get("created", {}).values())
    assert total_created == 0, f"Expected 0 created on reapply, got summary: {summary}"
