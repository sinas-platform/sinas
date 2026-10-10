"""LLM providers: one applier for config and the REST API.

Config apply kept its own copy of the write: it never adopted (or stamped) a
provider made in the console, let two config files overwrite each other's,
switched deleted providers back on and unset a default chosen in the
console. Export dropped the default model, the default flag and extra
config. Nothing was in the change history, and a key must never be.
"""

import json
import uuid

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.config_revision import ConfigRevision
from app.models.llm_provider import LLMProvider
from app.schemas.config import SinasConfig
from app.schemas.spec.llm_provider import LLMProviderSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers


def _name() -> str:
    return f"prov-{uuid.uuid4().hex[:8]}"


def _yaml(name: str, **extra) -> dict:
    return {
        "name": name, "type": "openai", "endpoint": "https://llm.example/v1",
        "models": ["m-large", "m-small"], "defaultModel": "m-large", **extra,
    }


async def _apply(db, owner, config_name="cfg", dry_run=False, **spec):
    config = SinasConfig.model_validate({
        "apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": config_name},
        "spec": spec,
    })
    svc = ConfigApplyService(
        db, config_name, owner_user_id=str(owner.id), managed_by="config", auto_commit=False
    )
    return await svc.apply_config(config, dry_run=dry_run)


async def _row(db: AsyncSession, name: str) -> LLMProvider | None:
    row = (await db.execute(select(LLMProvider).where(LLMProvider.name == name))).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _revisions(db: AsyncSession, name: str) -> list[ConfigRevision]:
    return list((await db.execute(
        select(ConfigRevision)
        .where(ConfigRevision.resource_kind == "llmProviders", ConfigRevision.resource_key == name)
        .order_by(ConfigRevision.id)
    )).scalars())


class TestSpec:
    def test_config_and_rest_shapes_are_one_spec(self):
        config = LLMProviderSpec.model_validate({**_yaml("p"), "config": {"api_version": "1"}})
        rest = LLMProviderSpec.model_validate({
            "name": "p", "provider_type": "openai", "api_endpoint": "https://llm.example/v1",
            "default_model": "m-large", "config": {"models": ["m-large", "m-small"], "api_version": "1"},
        })
        assert config == rest


class TestConfigApply:
    async def test_key_kept_when_left_out_and_never_in_history(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, llmProviders=[_yaml(name, apiKey="sk-first-123")])).success
        again = await _apply(db, admin_user, llmProviders=[_yaml(name)])
        assert again.success and not again.summary.updated, again.summary
        assert encryption_service.decrypt((await _row(db, name)).api_key) == "sk-first-123"
        assert (await _apply(db, admin_user, llmProviders=[_yaml(name, apiKey="sk-second-456")])).success
        row = await _row(db, name)
        assert encryption_service.decrypt(row.api_key) == "sk-second-456"
        revisions = await _revisions(db, name)
        assert [r.action for r in revisions] == ["create", "update"]
        recorded = json.dumps([r.spec for r in revisions]) + json.dumps([r.changes for r in revisions])
        assert "sk-" not in recorded and "sk-" not in (row.config_checksum or "")

    async def test_a_deleted_provider_stays_off_and_the_console_default_stays(self, client, db: AsyncSession, admin_user):
        a, b = _name(), _name()
        assert (await _apply(db, admin_user, llmProviders=[_yaml(a), _yaml(b, isDefault=True)])).success
        row_a = await _row(db, a)
        h = auth_headers(admin_user)
        assert (await client.patch(f"/api/v1/llm-providers/{row_a.id}", json={"is_default": True}, headers=h)).status_code == 200
        assert (await client.delete(f"/api/v1/llm-providers/{row_a.id}", headers=h)).status_code == 204
        # The console edit detached it; config adopts it back without
        # undoing the operator's state.
        assert (await _apply(db, admin_user, llmProviders=[_yaml(a, endpoint="https://other"), _yaml(b)])).success
        row_a, row_b = await _row(db, a), await _row(db, b)
        assert (row_a.api_endpoint, row_a.is_active, row_a.is_default, row_b.is_default) == (
            "https://other", False, True, False,
        )

    async def test_an_agent_on_a_provider_from_the_same_config(self, db: AsyncSession, admin_user):
        name = _name()
        agent = {"namespace": "bots", "name": f"a{uuid.uuid4().hex[:6]}", "llmProviderName": name}
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, llmProviders=[_yaml(name)], agents=[agent])
            assert result.success, result.errors

    async def test_another_config_files_provider_is_left_alone(self, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, config_name="a", llmProviders=[_yaml(name)])).success
        result = await _apply(db, admin_user, config_name="b", llmProviders=[_yaml(name, endpoint="https://b")])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).api_endpoint == "https://llm.example/v1"

    async def test_export_round_trips_and_keeps_the_key_out(self, db: AsyncSession, admin_user):
        name = _name()
        spec = _yaml(name, apiKey="sk-export-789", isDefault=True, config={"api_version": "2024"})
        assert (await _apply(db, admin_user, llmProviders=[spec])).success
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        exported = next(p for p in doc["llmProviders"] if p["name"] == name)
        assert "apiKey" not in exported
        assert (exported["defaultModel"], exported["isDefault"], exported["config"]) == ("m-large", True, {"api_version": "2024"})
        again = await _apply(db, admin_user, llmProviders=[exported])
        assert again.success and not again.summary.updated, (again.errors, again.summary)
        with_key = yaml.safe_load(await ConfigExportService(db, include_secrets=True).export_config())["spec"]
        assert next(p for p in with_key["llmProviders"] if p["name"] == name)["apiKey"] == "sk-export-789"


class TestRest:
    async def test_crud_is_recorded_and_restorable(self, client, db: AsyncSession, admin_user):
        name, h = _name(), auth_headers(admin_user)
        body = {"name": name, "provider_type": "openai", "api_key": "sk-rest-1", "config": {"models": ["m"]}}
        r = await client.post("/api/v1/llm-providers", json=body, headers=h)
        assert r.status_code == 201, r.text
        assert "api_key" not in r.json()
        r = await client.post("/api/v1/llm-providers", json=body, headers=h)
        assert r.status_code == 400 and "already exists" in r.text
        provider_id = r_id = (await _row(db, name)).id
        r = await client.patch(f"/api/v1/llm-providers/{r_id}", json={"default_model": "m"}, headers=h)
        assert r.status_code == 200 and r.json()["default_model"] == "m"
        assert encryption_service.decrypt((await _row(db, name)).api_key) == "sk-rest-1"  # kept
        assert (await client.delete(f"/api/v1/llm-providers/{provider_id}", headers=h)).status_code == 204
        assert (await _row(db, name)).is_active is False
        r = await client.patch(f"/api/v1/llm-providers/{provider_id}", json={"is_active": True}, headers=h)
        assert r.status_code == 200 and r.json()["is_active"] is True
        assert [r.action for r in await _revisions(db, name)] == ["create", "update", "update", "update"]

    async def test_making_one_default_records_the_previous_one(self, client, db: AsyncSession, admin_user):
        a, b, h = _name(), _name(), auth_headers(admin_user)
        await client.post("/api/v1/llm-providers", json={"name": a, "provider_type": "openai", "is_default": True}, headers=h)
        await client.post("/api/v1/llm-providers", json={"name": b, "provider_type": "openai"}, headers=h)
        b_id = (await _row(db, b)).id
        assert (await client.patch(f"/api/v1/llm-providers/{b_id}", json={"is_default": True}, headers=h)).status_code == 200
        defaults = (await db.execute(select(LLMProvider.name).where(LLMProvider.is_default.is_(True)))).scalars().all()
        assert defaults == [b]
        last = (await _revisions(db, a))[-1]
        assert last.changes == {"is_default": {"from": True, "to": False}}


class TestReviewFixes:
    async def test_a_blank_key_keeps_the_stored_one(self, client, db: AsyncSession, admin_user):
        name = _name()
        assert (await _apply(db, admin_user, llmProviders=[_yaml(name, apiKey="sk-keep-1")])).success
        assert (await _apply(db, admin_user, llmProviders=[_yaml(name, apiKey="")])).success
        row = await _row(db, name)
        assert encryption_service.decrypt(row.api_key) == "sk-keep-1"
        r = await client.patch(f"/api/v1/llm-providers/{row.id}", json={"api_key": ""}, headers=auth_headers(admin_user))
        assert r.status_code == 200, r.text
        assert encryption_service.decrypt((await _row(db, name)).api_key) == "sk-keep-1"

    async def test_an_unreadable_key_fails_a_secret_export(self, db: AsyncSession, admin_user):
        import pytest

        name = _name()
        db.add(LLMProvider(name=name, provider_type="openai", api_key="not-a-fernet-token"))
        await db.flush()
        with pytest.raises(ValueError, match=name):
            await ConfigExportService(db, include_secrets=True).export_config()
        # Without secrets the export still works.
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        assert any(p["name"] == name for p in doc["llmProviders"])

    async def test_default_changes_take_the_singleton_lock(self, db: AsyncSession, admin_user, monkeypatch):
        from app.services.resources import base

        taken = []
        original = base.lock_singleton

        async def spy(ctx, name):
            taken.append(name)
            await original(ctx, name)

        monkeypatch.setattr(base, "lock_singleton", spy)
        assert (await _apply(db, admin_user, llmProviders=[_yaml(_name(), isDefault=True)])).success
        # At the apply's start (before any row lock), and again (re-entrant)
        # where the previous default is unset.
        assert taken and set(taken) == {"default-llm-provider"}


async def test_every_promotion_takes_the_lock_before_row_locks(client, db: AsyncSession, admin_user, monkeypatch):
    """Lock order: config apply takes it at its start, REST before locking
    the row. Recorded against the row locks the appliers take."""
    from app.services.resources import base

    events = []
    original_lock, original_find = base.lock_singleton, None

    async def spy_lock(ctx, name):
        events.append(("singleton", name))
        await original_lock(ctx, name)

    monkeypatch.setattr(base, "lock_singleton", spy_lock)
    import app.api.v1.endpoints.llm_providers as endpoint

    monkeypatch.setattr(endpoint, "lock_singleton", spy_lock)
    from app.services.resources import rest

    original_locked = rest.locked

    async def spy_locked(applier, ctx, authorized):
        events.append(("row", applier.kind))
        return await original_locked(applier, ctx, authorized)

    monkeypatch.setattr(rest, "locked", spy_locked)
    name, h = _name(), auth_headers(admin_user)
    await client.post("/api/v1/llm-providers", json={"name": name, "provider_type": "openai"}, headers=h)
    events.clear()
    provider_id = (await _row(db, name)).id
    r = await client.patch(f"/api/v1/llm-providers/{provider_id}", json={"is_default": True}, headers=h)
    assert r.status_code == 200, r.text
    assert events[0] == ("singleton", "default-llm-provider")
    assert ("row", "llmProviders") in events
