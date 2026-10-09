"""Secrets in config apply and export.

`EncryptionService.encrypt` was called on the class instead of an instance,
so any config declaring an LLM provider `apiKey` or a database connection
`password` failed to apply — and since applies are all-or-nothing (and run
at boot with AUTO_APPLY_CONFIG), it failed the whole config. Export with
secrets had the same problem. Reported in #161.

Secrets are left out of the change checksum, so a rotated key alone used to
look "unchanged" and was ignored; the update tests below pin that too.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
from app.models.database_connection import DatabaseConnection
from app.models.llm_provider import LLMProvider
from app.schemas.config import SinasConfig
from app.services.config_apply.service import ConfigApplyService
from app.services.config_export import ConfigExportService


def _config(spec: dict) -> SinasConfig:
    return SinasConfig.model_validate(
        {"apiVersion": "sinas.co/v1", "kind": "SinasConfig", "metadata": {"name": "cfg"}, "spec": spec}
    )


async def _apply(db: AsyncSession, owner, spec: dict):
    svc = ConfigApplyService(db, "cfg", owner_user_id=str(owner.id), auto_commit=False)
    return await svc.apply_config(_config(spec))


class TestSecretsApply:
    async def test_llm_provider_api_key_is_stored_encrypted(self, db: AsyncSession, admin_user):
        name = f"prov-{uuid.uuid4().hex[:8]}"
        provider = {"name": name, "type": "openai", "apiKey": "sk-first", "models": ["gpt-x"]}

        result = await _apply(db, admin_user, {"llmProviders": [provider]})
        assert result.success, result.errors
        row = (await db.execute(select(LLMProvider).where(LLMProvider.name == name))).scalar_one()
        assert row.api_key != "sk-first"
        assert encryption_service.decrypt(row.api_key) == "sk-first"

        # Update path: a changed key replaces the stored one.
        result = await _apply(db, admin_user, {"llmProviders": [{**provider, "apiKey": "sk-second"}]})
        assert result.success, result.errors
        await db.flush()
        assert encryption_service.decrypt(row.api_key) == "sk-second"

    async def test_database_connection_password_is_stored_encrypted(self, db: AsyncSession, admin_user):
        name = f"conn-{uuid.uuid4().hex[:8]}"
        conn = {
            "name": name, "connectionType": "postgresql", "host": "db", "port": 5432,
            "database": "app", "username": "reader", "password": "first-pw",
        }

        result = await _apply(db, admin_user, {"databaseConnections": [conn]})
        assert result.success, result.errors
        row = (await db.execute(select(DatabaseConnection).where(DatabaseConnection.name == name))).scalar_one()
        assert encryption_service.decrypt(row.password) == "first-pw"

        result = await _apply(db, admin_user, {"databaseConnections": [{**conn, "password": "second-pw"}]})
        assert result.success, result.errors
        await db.flush()
        assert encryption_service.decrypt(row.password) == "second-pw"

    async def test_an_unchanged_secret_stays_unchanged(self, db: AsyncSession, admin_user):
        name = f"prov-{uuid.uuid4().hex[:8]}"
        provider = {"name": name, "type": "openai", "apiKey": "sk-same"}
        await _apply(db, admin_user, {"llmProviders": [provider]})
        result = await _apply(db, admin_user, {"llmProviders": [provider]})
        assert result.summary.unchanged.get("llmProviders") == 1
        # Leaving the key out keeps the stored one.
        result = await _apply(db, admin_user, {"llmProviders": [{"name": name, "type": "openai"}]})
        row = (await db.execute(select(LLMProvider).where(LLMProvider.name == name))).scalar_one()
        assert encryption_service.decrypt(row.api_key) == "sk-same"


class TestSecretsExport:
    async def test_export_with_secrets_decrypts_the_api_key(self, db: AsyncSession, admin_user):
        name = f"prov-{uuid.uuid4().hex[:8]}"
        await _apply(db, admin_user, {"llmProviders": [{"name": name, "type": "openai", "apiKey": "sk-export"}]})

        exported = await ConfigExportService(db, include_secrets=True)._export_llm_providers()
        [mine] = [p for p in exported if p["name"] == name]
        assert mine["apiKey"] == "sk-export"

        without = await ConfigExportService(db)._export_llm_providers()
        [mine] = [p for p in without if p["name"] == name]
        assert "apiKey" not in mine
