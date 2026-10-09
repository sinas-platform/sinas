"""Webhooks: one applier for every write channel.

The REST API and config apply were two hand-written write paths that had
drifted apart (references checked on one channel only, a path clash that was
a 400 for your own webhooks and a 500 for anyone else's, an export that could
not be applied again). These pin that both now behave identically, and that
every change is recorded in the change history.
"""

import uuid

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_revision import ConfigRevision
from app.models.function import Function
from app.models.webhook import Webhook
from app.schemas.config import SinasConfig
from app.schemas.spec.webhook import WebhookSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

FIELDS = (
    "target_type", "function_namespace", "function_name", "agent_namespace", "agent_name",
    "pipeline_namespace", "pipeline_name", "message_template", "session_key_template",
    "http_method", "description", "default_values", "is_active", "requires_auth",
    "response_mode", "dedup",
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _yaml_webhook(path: str, fn: Function, **extra) -> dict:
    return {
        "path": path,
        "functionName": f"{fn.namespace}/{fn.name}",
        "httpMethod": "POST",
        "description": "Inbound",
        "defaultValues": {"source": "hook"},
        "dedup": {"key": "$.id", "ttlSeconds": 600},
        **extra,
    }


def _rest_webhook(path: str, fn: Function, **extra) -> dict:
    return {
        "path": path,
        "function_namespace": fn.namespace,
        "function_name": fn.name,
        "http_method": "POST",
        "description": "Inbound",
        "default_values": {"source": "hook"},
        "dedup": {"key": "$.id", "ttl_seconds": 600},
        **extra,
    }


def _config(*webhooks: dict) -> SinasConfig:
    return SinasConfig.model_validate(
        {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasConfig",
            "metadata": {"name": "cfg"},
            "spec": {"webhooks": list(webhooks)},
        }
    )


async def _apply(db, owner, *webhooks, managed_by="config", dry_run=False):
    svc = ConfigApplyService(
        db, "cfg", owner_user_id=str(owner.id), managed_by=managed_by, auto_commit=False
    )
    return svc, await svc.apply_config(_config(*webhooks), dry_run=dry_run)


async def _row(db: AsyncSession, path: str) -> Webhook | None:
    return (await db.execute(select(Webhook).where(Webhook.path == path))).scalar_one_or_none()


async def _revisions(db: AsyncSession, path: str) -> list[ConfigRevision]:
    return list(
        (
            await db.execute(
                select(ConfigRevision)
                .where(ConfigRevision.resource_kind == "webhooks", ConfigRevision.resource_key == path)
                .order_by(ConfigRevision.id)
            )
        ).scalars()
    )


# ------------------------------------------------------------------ spec


class TestWebhookSpec:
    def test_rest_and_config_shapes_are_the_same_spec(self):
        config = WebhookSpec.model_validate({
            "path": "a/b", "targetType": "agent", "agentName": "support/triage",
            "messageTemplate": "{{ body }}",
        })
        rest = WebhookSpec.model_validate({
            "path": "a/b", "target_type": "agent", "target_namespace": "support",
            "target_name": "triage", "message_template": "{{ body }}",
        })
        assert config == rest

    def test_a_lowercase_method_is_accepted(self):
        """Config never validated it: "post" reached the database enum and
        failed the write, blamed on whichever resource flushed next."""
        spec = WebhookSpec.model_validate({"path": "x", "functionName": "f", "httpMethod": "post"})
        assert spec.http_method == "POST"

    @pytest.mark.parametrize("bad", [
        {"httpMethod": "FETCH"},
        {"dedup": {"key": "$.id", "ttlSeconds": 0}},  # every deduplicated call failed
        {"path": "has space"},
        {"targetType": "agent", "agentName": "a/b"},  # agent needs a message template
        {"responseMode": "raw", "targetType": "pipeline", "pipelineName": "p/q"},
    ])
    def test_config_now_refuses_what_never_worked(self, bad):
        with pytest.raises(ValidationError):
            WebhookSpec.model_validate({"path": "x", "functionName": "f", **bad})

    @pytest.mark.parametrize("worked", [
        {"dedup": {"key": "$.id", "ttlSeconds": 172800}},  # Redis took any positive TTL
        {"path": "stripe:events"},  # the runtime route serves any path
        {"functionName": "my.team/f"},  # function namespaces have no config pattern
    ])
    def test_config_keeps_accepting_what_worked(self, worked):
        """Applies are all-or-nothing and run at boot: refusing YAML that
        worked would take a running install down on upgrade."""
        WebhookSpec.model_validate({"path": "x", "functionName": "f", **worked})

    def test_only_the_targets_own_reference_is_kept(self):
        spec = WebhookSpec.model_validate({
            "path": "x", "targetType": "pipeline", "pipelineName": "crm/sync",
            "functionName": "stale/fn",
        })
        assert (spec.target_namespace, spec.target_name) == ("crm", "sync")

    def test_config_form_round_trips(self):
        spec = WebhookSpec.model_validate({
            "path": "x", "functionName": "ns/f", "dedup": {"key": "$.id", "ttlSeconds": 60},
            "isActive": False,
        })
        assert WebhookSpec.model_validate(spec.to_config()) == spec


# ------------------------------------------------------------ one write path


class TestOneWritePath:
    async def test_api_and_config_write_identical_rows(
        self, client, db: AsyncSession, admin_user, fn
    ):
        api_path, cfg_path = f"api/{_uid()}", f"cfg/{_uid()}"
        response = await client.post(
            "/api/v1/webhooks", json=_rest_webhook(api_path, fn), headers=auth_headers(admin_user)
        )
        assert response.status_code == 201, response.text
        _, result = await _apply(db, admin_user, _yaml_webhook(cfg_path, fn))
        assert result.success, result.errors

        api_row, cfg_row = await _row(db, api_path), await _row(db, cfg_path)
        await db.refresh(api_row)
        assert {f: getattr(api_row, f) for f in FIELDS} == {f: getattr(cfg_row, f) for f in FIELDS}
        assert cfg_row.dedup == {"key": "$.id", "ttl_seconds": 600}

    async def test_config_now_checks_the_target_exists(self, db: AsyncSession, admin_user):
        _, result = await _apply(db, admin_user, {"path": f"x/{_uid()}", "functionName": "nope/none"})
        assert not result.success
        assert "Function 'nope.none' not found" in result.errors[0]

    async def test_another_users_path_is_a_400_not_a_500(
        self, client, db: AsyncSession, admin_user, admin_role, fn
    ):
        from app.models.user import User, UserRole

        path = f"taken/{_uid()}"
        await _apply(db, admin_user, _yaml_webhook(path, fn))
        other = User(email=f"other-{_uid()}@example.com")
        db.add(other)
        await db.flush()
        db.add(UserRole(role_id=admin_role.id, user_id=other.id, active=True))
        own_fn = Function(
            user_id=other.id, namespace=f"ns{_uid()}", name="mine",
            code="def handler(input, context):\n    return {}", input_schema={}, output_schema={},
        )
        db.add(own_fn)
        await db.flush()
        response = await client.post(
            "/api/v1/webhooks", json=_rest_webhook(path, own_fn), headers=auth_headers(other)
        )
        assert response.status_code == 400
        assert response.json()["detail"] == f"Webhook path '{path}' already exists"

    async def test_every_api_change_is_recorded(self, client, db: AsyncSession, admin_user, fn):
        path, headers = f"h/{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/webhooks", json=_rest_webhook(path, fn), headers=headers)
        await client.patch(f"/api/v1/webhooks/{path}", json={"description": "x"}, headers=headers)
        await client.delete(f"/api/v1/webhooks/{path}", headers=headers)
        revisions = await _revisions(db, path)
        assert [r.action for r in revisions] == ["create", "update", "delete"]
        assert revisions[1].changes == {"description": {"from": "Inbound", "to": "x"}}


# ------------------------------------------------------------------ is_active


class TestIsActiveIsTheOperatorsUnlessDeclared:
    async def test_a_re_apply_does_not_re_arm_a_disabled_webhook(
        self, client, db: AsyncSession, admin_user, fn
    ):
        path = f"cfg/{_uid()}"
        await _apply(db, admin_user, _yaml_webhook(path, fn))
        await client.patch(
            f"/api/v1/webhooks/{path}", json={"is_active": False}, headers=auth_headers(admin_user)
        )
        _, result = await _apply(db, admin_user, _yaml_webhook(path, fn))
        assert result.success, result.errors
        row = await _row(db, path)
        await db.refresh(row)
        assert row.is_active is False

    async def test_a_declared_state_is_applied(self, db: AsyncSession, admin_user, fn):
        path = f"cfg/{_uid()}"
        await _apply(db, admin_user, _yaml_webhook(path, fn))
        await _apply(db, admin_user, _yaml_webhook(path, fn, isActive=False))
        assert (await _row(db, path)).is_active is False
        await _apply(db, admin_user, _yaml_webhook(path, fn, isActive=True))
        assert (await _row(db, path)).is_active is True


# ------------------------------------------------------------------ REST PATCH


class TestPatch:
    async def test_a_disabled_webhook_stays_reachable(self, client, admin_user, fn):
        """Lookups filtered on is_active: once disabled, a webhook was a 404
        for GET, PATCH and DELETE, so it could never be enabled again."""
        path, headers = f"h/{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/webhooks", json=_rest_webhook(path, fn), headers=headers)
        await client.patch(f"/api/v1/webhooks/{path}", json={"is_active": False}, headers=headers)

        assert (await client.get(f"/api/v1/webhooks/{path}", headers=headers)).status_code == 200
        enabled = await client.patch(f"/api/v1/webhooks/{path}", json={"is_active": True}, headers=headers)
        assert enabled.status_code == 200 and enabled.json()["is_active"] is True
        assert (await client.delete(f"/api/v1/webhooks/{path}", headers=headers)).status_code == 204

    async def test_switching_target_type_needs_the_new_targets_name(
        self, client, admin_user, fn, agent
    ):
        """A stale agent_name stored alongside a function target used to be
        picked up by a type-only PATCH, with no existence or permission check."""
        path, headers = f"h/{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/webhooks", json=_rest_webhook(path, fn), headers=headers)

        bare = await client.patch(
            f"/api/v1/webhooks/{path}",
            json={"target_type": "agent", "message_template": "m"}, headers=headers,
        )
        assert bare.status_code == 422
        assert "agent_name is required" in bare.text

        missing = await client.patch(
            f"/api/v1/webhooks/{path}",
            json={"target_type": "agent", "agent_namespace": "no", "agent_name": "such",
                  "message_template": "m"},
            headers=headers,
        )
        assert missing.status_code == 404

        ok = await client.patch(
            f"/api/v1/webhooks/{path}",
            json={"target_type": "agent", "agent_namespace": agent.namespace,
                  "agent_name": agent.name, "message_template": "m"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert (body["agent_name"], body["function_name"]) == (agent.name, None)

    async def test_a_legacy_webhook_with_dedup_can_still_be_disabled(
        self, client, db: AsyncSession, admin_user, fn
    ):
        """A stored invalid field is tolerated by PATCH; building the spec from
        dumped values left dedup a dict, and the write failed with a 500."""
        path = f"legacy/{_uid()}"
        db.add(Webhook(
            user_id=admin_user.id, path=path, function_namespace=fn.namespace,
            function_name=fn.name, dedup={"key": "$.id", "ttl_seconds": 0},
        ))
        await db.flush()
        response = await client.patch(
            f"/api/v1/webhooks/{path}", json={"is_active": False}, headers=auth_headers(admin_user)
        )
        assert response.status_code == 200, response.text
        assert response.json()["dedup"] == {"key": "$.id", "ttl_seconds": 0}

    async def test_null_clears_dedup_and_session_key(self, client, admin_user, fn):
        path, headers = f"h/{_uid()}", auth_headers(admin_user)
        await client.post(
            "/api/v1/webhooks", json=_rest_webhook(path, fn, session_key_template="k"), headers=headers
        )
        response = await client.patch(
            f"/api/v1/webhooks/{path}", json={"dedup": None, "session_key_template": None},
            headers=headers,
        )
        assert (response.json()["dedup"], response.json()["session_key_template"]) == (None, None)

    async def test_a_manual_edit_detaches_a_config_managed_webhook(
        self, client, db: AsyncSession, admin_user, fn
    ):
        """REST used to detach only package rows; a config row edited by hand
        stayed config-managed, so the next apply silently reverted the edit."""
        path = f"cfg/{_uid()}"
        await _apply(db, admin_user, _yaml_webhook(path, fn))
        await client.patch(
            f"/api/v1/webhooks/{path}", json={"description": "by hand"},
            headers=auth_headers(admin_user),
        )
        row = await _row(db, path)
        await db.refresh(row)
        assert row.managed_by is None


# ------------------------------------------------------------------ export


class TestExport:
    async def test_an_export_can_be_applied_again(self, db: AsyncSession, admin_user, fn):
        """httpMethod was exported as a python object tag that safe_load
        refuses, so no export containing a webhook could be applied."""
        path = f"cfg/{_uid()}"
        await _apply(db, admin_user, _yaml_webhook(path, fn, isActive=False))
        exported = await ConfigExportService(db, managed_only=True)._export_webhooks()
        [mine] = yaml.safe_load(yaml.dump([w for w in exported if w["path"] == path]))
        assert mine["httpMethod"] == "POST"
        assert mine["isActive"] is False  # disabled webhooks are exported, not dropped

        _, result = await _apply(db, admin_user, mine)
        assert result.summary.unchanged.get("webhooks") == 1


# ------------------------------------------------------------------ packages


class TestPackageUninstall:
    async def test_uninstall_records_the_deletion(self, db: AsyncSession, admin_user, published):
        from app.services.package_service import PackageService

        pkg, ns, path = f"pkg-{_uid()}", f"ns{_uid()}", f"pkg/{_uid()}"
        package = "\n".join([
            "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
            "package:", f"  name: {pkg}", '  version: "1.0.0"', "spec:", "  functions:",
            f"    - namespace: {ns}", "      name: job", "      code: |",
            "        def handler(input, context):", "            return {}",
            "  webhooks:", f"    - path: {path}", f"      functionName: {ns}/job",
        ]) + "\n"
        service = PackageService(db)
        await service.install(package, str(admin_user.id))
        assert (await _row(db, path)).managed_by == f"pkg:{pkg}"

        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("webhooks") == 1
        assert await _row(db, path) is None
        assert [r.action for r in await _revisions(db, path)] == ["create", "delete"]
