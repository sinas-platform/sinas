"""Agents: one applier for every write channel.

Config apply kept its own copy of the write: it brought deleted agents back,
unset a default chosen in the console, overwrote hand-made agents (so
packages took them over) and stored camelCase hooks the runtime never read.
REST turned temperature 0 into 0.7, 500'd on a rename clash or an unknown
provider, and couldn't restore a deleted agent. Nothing was in the change
history, and package upgrades never removed an agent they dropped.
"""

import uuid

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.chat import Chat
from app.models.config_revision import ConfigRevision
from app.models.llm_provider import LLMProvider
from app.schemas.config import SinasConfig
from app.schemas.spec.agent import AgentSpec
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService
from tests.conftest import auth_headers

NS = "bots"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def provider(db: AsyncSession) -> LLMProvider:
    row = LLMProvider(name=f"prov-{_uid()}", provider_type="openai", default_model="gpt-x")
    db.add(row)
    await db.flush()
    return row


def _yaml(name: str, **extra) -> dict:
    return {
        "namespace": NS, "name": name, "description": "Helps", "systemPrompt": "Be brief.",
        "enabledFunctions": ["tools/search"], "enabledStores": ["crm/notes"], **extra,
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


async def _row(db: AsyncSession, name: str) -> Agent | None:
    row = (
        await db.execute(select(Agent).where(Agent.namespace == NS, Agent.name == name))
    ).scalar_one_or_none()
    if row is not None:
        await db.refresh(row)
    return row


async def _actions(db: AsyncSession, name: str) -> list[str]:
    return list((await db.execute(
        select(ConfigRevision.action)
        .where(ConfigRevision.resource_kind == "agents", ConfigRevision.resource_key == f"{NS}/{name}")
        .order_by(ConfigRevision.id)
    )).scalars())


class TestSpec:
    def test_config_normalizations_hold_on_every_channel(self):
        spec = AgentSpec.model_validate({
            "name": "a",
            "enabledFunctions": ["search", "tools/x"],
            "enabledSkills": ["faq", {"skill": "kb/howto", "preload": True}],
            "enabledStores": ["crm/notes", {"store": "crm/log"}],
            "enabledCollections": ["docs"],
            "hooks": {"onUserMessage": [{"function": "guard/check", "onTimeout": "block"}]},
            "temperature": 0,
        })
        assert spec.enabled_functions == ["default/search", "tools/x"]
        assert [(s.skill, s.preload) for s in spec.enabled_skills] == [("default/faq", False), ("kb/howto", True)]
        # A bare store name means read-write (config's rule); an object defaults to read-only.
        assert [(s.store, s.access) for s in spec.enabled_stores] == [("crm/notes", "readwrite"), ("crm/log", "readonly")]
        assert [(c.collection, c.access) for c in spec.enabled_collections] == [("default/docs", "readonly")]
        # Stored the way the runtime reads hooks.
        assert spec.hooks == {"on_user_message": [{"function": "guard/check", "on_timeout": "block"}]}
        assert spec.temperature == 0

    def test_misspelt_fields_are_errors(self):
        with pytest.raises(ValidationError):
            AgentSpec.model_validate({"name": "a", "systemPromt": "x"})


class TestConfigApply:
    async def test_create_unchanged_update(self, db: AsyncSession, admin_user, provider):
        name = f"a{_uid()}"
        spec = _yaml(name, llmProviderName=provider.name)
        assert (await _apply(db, admin_user, agents=[spec])).success
        row = await _row(db, name)
        assert row.llm_provider_id == provider.id
        assert row.enabled_stores == [{"store": "crm/notes", "access": "readwrite"}]
        again = await _apply(db, admin_user, agents=[spec])
        assert again.success and not again.summary.updated, again.summary
        assert (await _apply(db, admin_user, agents=[{**spec, "temperature": 0.2}])).success
        assert (await _row(db, name)).temperature == 0.2
        assert await _actions(db, name) == ["create", "update"]

    async def test_an_unknown_provider_fails_in_preview_too(self, db: AsyncSession, admin_user):
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, agents=[_yaml(f"a{_uid()}", llmProviderName="nope")])
            assert not result.success and "not found" in result.errors[0], result.errors

    async def test_a_deleted_agent_stays_deleted(self, client, db: AsyncSession, admin_user):
        name = f"a{_uid()}"
        assert (await _apply(db, admin_user, agents=[_yaml(name)])).success
        r = await client.delete(f"/api/v1/agents/{NS}/{name}", headers=auth_headers(admin_user))
        assert r.status_code == 204
        assert (await _apply(db, admin_user, agents=[_yaml(name, description="v2")])).success
        row = await _row(db, name)
        assert (row.description, row.is_active) == ("v2", False)
        # A webhook on it fails in the preview just as in the real apply.
        hook = {"path": f"h/{_uid()}", "targetType": "agent", "agentName": f"{NS}/{name}", "messageTemplate": "m"}
        for dry_run in (True, False):
            result = await _apply(db, admin_user, dry_run=dry_run, agents=[_yaml(name, description="v2")], webhooks=[hook])
            assert not result.success, dry_run
        assert (await _apply(db, admin_user, agents=[_yaml(name, isActive=True)])).success
        assert (await _row(db, name)).is_active is True

    async def test_one_default_and_a_console_default_is_kept(self, db: AsyncSession, admin_user):
        a, b = f"a{_uid()}", f"b{_uid()}"
        assert (await _apply(db, admin_user, agents=[_yaml(a, isDefault=True), _yaml(b)])).success
        assert (await _row(db, a)).is_default is True
        assert (await _apply(db, admin_user, agents=[_yaml(a), _yaml(b, isDefault=True)])).success
        assert ((await _row(db, a)).is_default, (await _row(db, b)).is_default) == (False, True)
        # Left unset, the default stays where it is.
        assert (await _apply(db, admin_user, agents=[_yaml(a, description="x"), _yaml(b, description="x")])).success
        assert (await _row(db, b)).is_default is True

    async def test_another_config_files_agent_is_left_alone(self, db: AsyncSession, admin_user):
        name = f"a{_uid()}"
        assert (await _apply(db, admin_user, config_name="a", agents=[_yaml(name)])).success
        result = await _apply(db, admin_user, config_name="b", agents=[_yaml(name, description="b")])
        assert result.success and any("config 'a'" in w for w in result.warnings)
        assert (await _row(db, name)).description == "Helps"

    async def test_export_round_trips_including_deleted(self, db: AsyncSession, admin_user, provider):
        name = f"a{_uid()}"
        spec = _yaml(name, llmProviderName=provider.name, isActive=False, providerOverrides={"prompt_caching": False})
        assert (await _apply(db, admin_user, agents=[spec])).success
        doc = yaml.safe_load(await ConfigExportService(db).export_config())["spec"]
        exported = next(a for a in doc["agents"] if a["name"] == name)
        assert (exported["isActive"], exported["llmProviderName"]) == (False, provider.name)
        assert exported["providerOverrides"] == {"prompt_caching": False}
        again = await _apply(db, admin_user, agents=[exported])
        assert again.success and not again.summary.updated, (again.errors, again.summary)


class TestRest:
    async def test_crud_is_recorded_and_delete_is_restorable(self, client, db: AsyncSession, admin_user, provider):
        name, h = f"a{_uid()}", auth_headers(admin_user)
        body = {"namespace": NS, "name": name, "temperature": 0, "llm_provider_id": str(provider.id)}
        r = await client.post("/api/v1/agents", json=body, headers=h)
        assert r.status_code == 201, r.text
        assert (r.json()["temperature"], r.json()["llm_provider_id"]) == (0, str(provider.id))
        assert (await client.delete(f"/api/v1/agents/{NS}/{name}", headers=h)).status_code == 204
        assert (await client.get(f"/api/v1/agents/{NS}/{name}", headers=h)).status_code == 404
        r = await client.post("/api/v1/agents", json=body, headers=h)
        assert r.status_code == 400 and "was deleted" in r.text
        r = await client.put(f"/api/v1/agents/{NS}/{name}", json={"is_active": True}, headers=h)
        assert r.status_code == 200, r.text
        assert (await client.get(f"/api/v1/agents/{NS}/{name}", headers=h)).status_code == 200
        assert await _actions(db, name) == ["create", "update", "update"]

    async def test_bad_provider_and_rename_clash_are_clear_errors(self, client, admin_user):
        a, b, h = f"a{_uid()}", f"b{_uid()}", auth_headers(admin_user)
        r = await client.post(
            "/api/v1/agents", json={"namespace": NS, "name": a, "llm_provider_id": str(uuid.uuid4())}, headers=h
        )
        assert r.status_code == 404
        for name in (a, b):
            assert (await client.post("/api/v1/agents", json={"namespace": NS, "name": name}, headers=h)).status_code == 201
        r = await client.put(f"/api/v1/agents/{NS}/{a}", json={"name": b}, headers=h)
        assert r.status_code == 400 and "already exists" in r.text

    async def test_making_one_default_unsets_the_others(self, client, db: AsyncSession, admin_user):
        a, b, h = f"a{_uid()}", f"b{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/agents", json={"namespace": NS, "name": a, "is_default": True}, headers=h)
        await client.post("/api/v1/agents", json={"namespace": NS, "name": b}, headers=h)
        r = await client.put(f"/api/v1/agents/{NS}/{b}", json={"is_default": True}, headers=h)
        assert r.status_code == 200, r.text
        defaults = (await db.execute(select(Agent.name).where(Agent.is_default.is_(True)))).scalars().all()
        assert defaults == [b]

    async def test_an_edit_detaches_a_package_agent(self, client, db: AsyncSession, admin_user):
        name = f"a{_uid()}"
        db.add(Agent(namespace=NS, name=name, user_id=admin_user.id, managed_by="pkg:x", config_name="x"))
        await db.flush()
        r = await client.put(f"/api/v1/agents/{NS}/{name}", json={"description": "ours"}, headers=auth_headers(admin_user))
        assert r.status_code == 200, r.text
        assert (await _row(db, name)).managed_by is None


def _package(pkg: str, version: str, names: list[str]) -> str:
    lines = [
        "apiVersion: sinas.co/v1", "kind: SinasPackage", "metadata:", f"  name: {pkg}",
        "package:", f"  name: {pkg}", f'  version: "{version}"', "spec:",
        "  agents:" + ("" if names else " []"),
    ]
    lines += [f"    - {{namespace: {NS}, name: {name}}}" for name in names]
    return "\n".join(lines) + "\n"


class TestPackages:
    async def test_upgrade_prunes_and_chats_survive_unlinked(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        pkg, keep, drop = f"pkg-{_uid()}", f"k{_uid()}", f"d{_uid()}"
        service = PackageService(db)
        _, first = await service.install(_package(pkg, "1.0.0", [keep, drop]), str(admin_user.id))
        assert first.success, first.errors
        dropped = await _row(db, drop)
        chat = Chat(user_id=admin_user.id, agent_id=dropped.id, agent_namespace=NS, agent_name=drop, title="t")
        db.add(chat)
        await db.flush()

        _, second = await service.install(_package(pkg, "2.0.0", [keep]), str(admin_user.id))
        assert second.success, second.errors
        assert second.summary.deleted == {"agents": 1}
        assert await _row(db, drop) is None
        await db.refresh(chat)
        assert (chat.agent_id, chat.agent_name) == (None, drop)

        counts = await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert counts.get("agents") == 1
        assert await _actions(db, keep) == ["create", "delete"]
        assert await _actions(db, drop) == ["create", "delete"]

    async def test_a_package_never_takes_over_a_hand_made_agent(self, db: AsyncSession, admin_user):
        from app.services.package_service import PackageService

        name = f"h{_uid()}"
        db.add(Agent(namespace=NS, name=name, user_id=admin_user.id, description="mine"))
        await db.flush()
        await PackageService(db).install(_package(f"pkg-{_uid()}", "1.0.0", [name]), str(admin_user.id))
        row = await _row(db, name)
        assert (row.description, row.managed_by) == ("mine", None)


class TestReviewFixes:
    async def test_older_string_references_survive_an_edit(self, client, db: AsyncSession, admin_user):
        name = f"o{_uid()}"
        db.add(Agent(
            namespace=NS, name=name, user_id=admin_user.id,
            enabled_skills=["kb/faq"], enabled_stores=["crm/notes"], enabled_collections=["docs/manuals"],
        ))
        await db.flush()
        r = await client.put(f"/api/v1/agents/{NS}/{name}", json={"description": "d"}, headers=auth_headers(admin_user))
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert row.enabled_skills == [{"skill": "kb/faq", "preload": False}]
        assert row.enabled_stores == [{"store": "crm/notes", "access": "readonly"}]
        assert row.enabled_collections == [{"collection": "docs/manuals", "access": "readonly"}]

    async def test_the_previous_default_is_recorded_too(self, client, db: AsyncSession, admin_user):
        a, b, h = f"a{_uid()}", f"b{_uid()}", auth_headers(admin_user)
        await client.post("/api/v1/agents", json={"namespace": NS, "name": a, "is_default": True}, headers=h)
        await client.post("/api/v1/agents", json={"namespace": NS, "name": b}, headers=h)
        await client.put(f"/api/v1/agents/{NS}/{b}", json={"is_default": True}, headers=h)
        last = (await db.execute(
            select(ConfigRevision).where(
                ConfigRevision.resource_kind == "agents", ConfigRevision.resource_key == f"{NS}/{a}"
            ).order_by(ConfigRevision.id.desc()).limit(1)
        )).scalar_one()
        assert (last.action, last.changes) == ("update", {"is_default": {"from": True, "to": False}})

    async def test_a_hard_deleted_agent_restores_as_it_was(self, client, db: AsyncSession, admin_user, provider):
        from app.services.package_service import PackageService

        pkg, name = f"pkg-{_uid()}", f"r{_uid()}"
        yaml_text = _package(pkg, "1.0.0", [name]).replace(
            f"{{namespace: {NS}, name: {name}}}",
            f"{{namespace: {NS}, name: {name}, llmProviderName: {provider.name}}}",
        )
        service = PackageService(db)
        _, installed = await service.install(yaml_text, str(admin_user.id))
        assert installed.success, installed.errors
        original = await _row(db, name)
        original_id, owner = original.id, original.user_id
        await service.uninstall(pkg, actor_user_id=str(admin_user.id))
        assert await _row(db, name) is None

        deleted = (await db.execute(
            select(ConfigRevision.id).where(
                ConfigRevision.resource_kind == "agents", ConfigRevision.resource_key == f"{NS}/{name}",
                ConfigRevision.action == "delete",
            )
        )).scalar_one()
        r = await client.post(f"/api/v1/config/history/{deleted}/restore", headers=auth_headers(admin_user))
        assert r.status_code == 200, r.text
        row = await _row(db, name)
        assert (row.id, row.user_id, row.llm_provider_id, row.is_active) == (original_id, owner, provider.id, True)
