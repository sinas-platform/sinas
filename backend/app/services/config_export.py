"""
Configuration export service
Exports current database state to declarative YAML format
"""
import logging

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from app.models.agent import Agent
from app.models.component import Component
from app.models.connector import Connector
from app.models.dependency import Dependency
from app.models.file import Collection
from app.models.function import Function
from app.models.llm_provider import LLMProvider
from app.models.manifest import Manifest
from app.models.query import Query
from app.models.secret import Secret
from app.models.skill import Skill
from app.models.store import Store

from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.schedule import ScheduledJob
from app.models.template import Template
from app.models.user import Role, RolePermission, User
from app.models.webhook import Webhook

from app.services.resource_serializers import (
    _remove_none_values,
    serialize_agent,
    serialize_collection,
    serialize_component,
    serialize_connector,
    serialize_database_trigger,
    serialize_function,
    serialize_manifest,
    serialize_query,
    serialize_schedule,
    serialize_skill,
    serialize_store,
    serialize_template,
    serialize_webhook,
)

logger = logging.getLogger(__name__)


class ConfigExportService:
    """Service for exporting current state to YAML configuration"""

    def __init__(self, db: AsyncSession, include_secrets: bool = False, managed_only: bool = False, managed_by: str = "config"):
        self.db = db
        self.include_secrets = include_secrets
        self.managed_only = managed_only
        self.managed_by = managed_by

    async def export_config(self) -> str:
        """Export current configuration to YAML string"""
        config_dict = {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasConfig",
            "metadata": {"name": "exported-config", "description": "Exported from SINAS database"},
            "spec": {},
        }

        # Export all resource types
        config_dict["spec"]["roles"] = await self._export_roles()
        config_dict["spec"]["users"] = await self._export_users()
        config_dict["spec"]["llmProviders"] = await self._export_llm_providers()


        config_dict["spec"]["dependencies"] = await self._export_dependencies()
        config_dict["spec"]["secrets"] = await self._export_secrets()
        config_dict["spec"]["connectors"] = await self._export_connectors()
        config_dict["spec"]["collections"] = await self._export_collections()
        config_dict["spec"]["queries"] = await self._export_queries()
        config_dict["spec"]["functions"] = await self._export_functions()
        config_dict["spec"]["skills"] = await self._export_skills()
        config_dict["spec"]["templates"] = await self._export_templates()
        config_dict["spec"]["stores"] = await self._export_stores()
        config_dict["spec"]["components"] = await self._export_components()
        config_dict["spec"]["manifests"] = await self._export_manifests()
        config_dict["spec"]["agents"] = await self._export_agents()
        config_dict["spec"]["webhooks"] = await self._export_webhooks()
        config_dict["spec"]["schedules"] = await self._export_schedules()
        config_dict["spec"]["databaseTriggers"] = await self._export_database_triggers()

        # Convert to YAML
        return yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    async def _export_roles(self) -> list[dict]:
        """Export roles"""
        stmt = select(Role)
        if self.managed_only:
            stmt = stmt.where(Role.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        roles = result.scalars().all()

        exported = []
        for role in roles:
            role_dict = {
                "name": role.name,
                "description": role.description,
            }
            if role.email_domain:
                role_dict["emailDomain"] = role.email_domain

            # Export permissions
            perm_stmt = select(RolePermission).where(RolePermission.role_id == role.id)
            perm_result = await self.db.execute(perm_stmt)
            permissions = perm_result.scalars().all()
            if permissions:
                role_dict["permissions"] = [
                    {"key": p.permission_key, "value": p.permission_value} for p in permissions
                ]

            exported.append(role_dict)

        return exported

    async def _export_users(self) -> list[dict]:
        """Export users"""
        stmt = select(User)
        if self.managed_only:
            stmt = stmt.where(User.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        users = result.scalars().all()

        exported = []
        for user in users:
            # Get user roles
            from app.models.user import UserRole

            member_stmt = select(UserRole).where(UserRole.user_id == user.id)
            member_result = await self.db.execute(member_stmt)
            memberships = member_result.scalars().all()

            role_stmt = select(Role).where(Role.id.in_([m.role_id for m in memberships]))
            role_result = await self.db.execute(role_stmt)
            roles = role_result.scalars().all()

            user_dict = {
                "email": user.email,
                "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
                "roles": [r.name for r in roles],
            }

            exported.append(user_dict)

        return exported

    async def _export_llm_providers(self) -> list[dict]:
        """Export LLM providers"""
        stmt = select(LLMProvider).where(LLMProvider.is_active == True)
        if self.managed_only:
            stmt = stmt.where(LLMProvider.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        providers = result.scalars().all()

        exported = []
        for provider in providers:
            provider_dict = {
                "name": provider.name,
                "type": provider.provider_type,
                "models": provider.config.get("models", []) if provider.config else [],
                "isActive": provider.is_active,
            }
            if provider.api_endpoint:
                provider_dict["endpoint"] = provider.api_endpoint

            if self.include_secrets and provider.api_key:
                provider_dict["apiKey"] = EncryptionService.decrypt(provider.api_key)

            exported.append(provider_dict)

        return exported

    async def _export_dependencies(self) -> list[dict]:
        """Export Python dependencies."""
        result = await self.db.execute(select(Dependency))
        dependencies = result.scalars().all()

        exported = []
        for dep in dependencies:
            dep_dict = {
                "packageName": dep.package_name,
                "version": dep.version,
            }
            exported.append(_remove_none_values(dep_dict))

        return exported

    async def _export_secrets(self) -> list[dict]:
        """Export secrets (names and descriptions only, never values)."""
        stmt = select(Secret)
        if self.managed_only:
            stmt = stmt.where(Secret.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        secrets = result.scalars().all()

        exported = []
        for secret in secrets:
            secret_dict = {
                "name": secret.name,
                "description": secret.description,
                # value intentionally omitted — secrets are write-only
            }
            exported.append(_remove_none_values(secret_dict))

        return exported

    async def _export_connectors(self) -> list[dict]:
        """Export connectors."""
        stmt = select(Connector).where(Connector.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Connector.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_connector(c) for c in result.scalars().all()]

    async def _export_functions(self) -> list[dict]:
        """Export functions"""
        stmt = select(Function).where(Function.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Function.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_function(f) for f in result.scalars().all()]

    async def _export_agents(self) -> list[dict]:
        """Export agents"""
        stmt = select(Agent).where(Agent.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Agent.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        agents = result.scalars().all()

        exported = []
        for agent in agents:
            provider_name = None
            if agent.llm_provider_id:
                provider_result = await self.db.execute(
                    select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
                )
                provider = provider_result.scalar_one_or_none()
                if provider:
                    provider_name = provider.name
            exported.append(serialize_agent(agent, provider_name))
        return exported

    async def _export_collections(self) -> list[dict]:
        """Export collections"""
        stmt = select(Collection)
        if self.managed_only:
            stmt = stmt.where(Collection.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_collection(c) for c in result.scalars().all()]

    async def _export_queries(self) -> list[dict]:
        """Export queries"""
        stmt = select(Query)
        if self.managed_only:
            stmt = stmt.where(Query.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        queries = result.scalars().all()

        exported = []
        for query in queries:
            conn_name = None
            if query.database_connection_id:
                conn_result = await self.db.execute(
                    select(DatabaseConnection).where(DatabaseConnection.id == query.database_connection_id)
                )
                conn = conn_result.scalar_one_or_none()
                if conn:
                    conn_name = conn.name
            exported.append(serialize_query(query, conn_name))
        return exported

    async def _export_skills(self) -> list[dict]:
        """Export skills"""
        stmt = select(Skill)
        if self.managed_only:
            stmt = stmt.where(Skill.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_skill(s) for s in result.scalars().all()]

    async def _export_components(self) -> list[dict]:
        """Export components"""
        stmt = select(Component)
        if self.managed_only:
            stmt = stmt.where(Component.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_component(c) for c in result.scalars().all()]

    async def _export_manifests(self) -> list[dict]:
        """Export manifests"""
        stmt = select(Manifest)
        if self.managed_only:
            stmt = stmt.where(Manifest.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_manifest(m) for m in result.scalars().all()]

    async def _export_stores(self) -> list[dict]:
        """Export stores"""
        stmt = select(Store)
        if self.managed_only:
            stmt = stmt.where(Store.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_store(s) for s in result.scalars().all()]

    async def _export_webhooks(self) -> list[dict]:
        """Export webhooks"""
        stmt = select(Webhook).where(Webhook.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Webhook.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_webhook(w) for w in result.scalars().all()]

    async def _export_templates(self) -> list[dict]:
        """Export templates"""
        stmt = select(Template).where(Template.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Template.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_template(t) for t in result.scalars().all()]

    async def _export_schedules(self) -> list[dict]:
        """Export scheduled jobs"""
        stmt = select(ScheduledJob).where(ScheduledJob.is_active == True)
        if self.managed_only:
            stmt = stmt.where(ScheduledJob.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_schedule(s) for s in result.scalars().all()]

    async def _export_database_triggers(self) -> list[dict]:
        """Export database triggers"""
        stmt = select(DatabaseTrigger).where(DatabaseTrigger.is_active == True)
        if self.managed_only:
            stmt = stmt.where(DatabaseTrigger.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        triggers = result.scalars().all()

        exported = []
        for trigger in triggers:
            conn_name = None
            if trigger.database_connection_id:
                conn_result = await self.db.execute(
                    select(DatabaseConnection).where(
                        DatabaseConnection.id == trigger.database_connection_id
                    )
                )
                conn = conn_result.scalar_one_or_none()
                if conn:
                    conn_name = conn.name
            exported.append(serialize_database_trigger(trigger, conn_name))
        return exported
