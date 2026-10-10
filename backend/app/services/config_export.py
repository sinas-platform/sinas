"""
Configuration export service
Exports current database state to declarative YAML format
"""
import logging

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encryption_service
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
        config_dict["spec"]["pipelines"] = await self._export_pipelines()
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

            if user.custom_fields:
                user_dict["customFields"] = user.custom_fields

            from app.models.user import UserIdentity

            identity_stmt = select(UserIdentity).where(UserIdentity.user_id == user.id)
            identity_result = await self.db.execute(identity_stmt)
            identities = identity_result.scalars().all()
            if identities:
                user_dict["identities"] = [
                    {
                        "provider": i.provider,
                        "subject": i.subject,
                        **({"metadata": i.identity_metadata} if i.identity_metadata else {}),
                    }
                    for i in identities
                ]

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
                provider_dict["apiKey"] = encryption_service.decrypt(provider.api_key)

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
        """Export connectors, disabled ones included (as isActive: false)."""
        stmt = select(Connector).order_by(Connector.namespace, Connector.name)
        if self.managed_only:
            stmt = stmt.where(Connector.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_connector(c) for c in result.scalars().all()]

    async def _export_pipelines(self) -> list[dict]:
        """Export pipelines (cursor/failure state is runtime, not exported)."""
        from app.models.pipeline import Pipeline
        from app.services.resource_serializers import serialize_pipeline

        stmt = select(Pipeline).where(Pipeline.is_active == True)
        if self.managed_only:
            stmt = stmt.where(Pipeline.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_pipeline(p) for p in result.scalars().all()]

    async def _export_functions(self) -> list[dict]:
        """Export functions, disabled ones included (as isActive: false):
        leaving them out dropped them from any instance restored from it."""
        stmt = select(Function).order_by(Function.namespace, Function.name)
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
        """Export collections."""
        stmt = select(Collection).order_by(Collection.namespace, Collection.name)
        if self.managed_only:
            stmt = stmt.where(Collection.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_collection(c) for c in result.scalars().all()]

    async def _export_queries(self) -> list[dict]:
        """Export queries, disabled ones included (as isActive: false)."""
        from app.services.resources.queries import connection_name

        stmt = select(Query).order_by(Query.namespace, Query.name)
        if self.managed_only:
            stmt = stmt.where(Query.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [
            serialize_query(query, await connection_name(self.db, query.database_connection_id))
            for query in result.scalars().all()
        ]

    async def _export_skills(self) -> list[dict]:
        """Export skills, disabled ones included (as isActive: false)."""
        stmt = select(Skill).order_by(Skill.namespace, Skill.name)
        if self.managed_only:
            stmt = stmt.where(Skill.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_skill(s) for s in result.scalars().all()]

    async def _export_components(self) -> list[dict]:
        """Export components, disabled ones included (as isActive: false)."""
        stmt = select(Component).order_by(Component.namespace, Component.name)
        if self.managed_only:
            stmt = stmt.where(Component.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_component(c) for c in result.scalars().all()]

    async def _export_manifests(self) -> list[dict]:
        """Export manifests, disabled ones included (as isActive: false)."""
        stmt = select(Manifest).order_by(Manifest.namespace, Manifest.name)
        if self.managed_only:
            stmt = stmt.where(Manifest.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_manifest(m) for m in result.scalars().all()]

    async def _export_stores(self) -> list[dict]:
        """Export stores."""
        stmt = select(Store).order_by(Store.namespace, Store.name)
        if self.managed_only:
            stmt = stmt.where(Store.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_store(s) for s in result.scalars().all()]

    async def _export_webhooks(self) -> list[dict]:
        """Export webhooks — disabled ones too, with isActive: false, as for
        schedules: leaving them out dropped them from any instance restored
        from the export."""
        stmt = select(Webhook).order_by(Webhook.path)
        if self.managed_only:
            stmt = stmt.where(Webhook.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_webhook(w) for w in result.scalars().all()]

    async def _export_templates(self) -> list[dict]:
        """Export templates, disabled ones included (as isActive: false):
        leaving them out dropped them from any instance restored from it."""
        stmt = select(Template).order_by(Template.namespace, Template.name)
        if self.managed_only:
            stmt = stmt.where(Template.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_template(t) for t in result.scalars().all()]

    async def _export_schedules(self) -> list[dict]:
        """Export scheduled jobs"""
        # Every schedule, paused ones included: is_active means "paused" for a
        # schedule, not "deleted", and a paused schedule left out of an export
        # vanished from any instance restored from it. Ordered, so the same
        # state always exports the same document.
        stmt = select(ScheduledJob).order_by(ScheduledJob.name)
        if self.managed_only:
            stmt = stmt.where(ScheduledJob.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_schedule(s) for s in result.scalars().all()]

    async def _export_database_triggers(self) -> list[dict]:
        """Export database triggers, paused ones included (see webhooks)."""
        stmt = (
            select(DatabaseTrigger, DatabaseConnection.name)
            .outerjoin(
                DatabaseConnection,
                DatabaseConnection.id == DatabaseTrigger.database_connection_id,
            )
            .order_by(DatabaseTrigger.name)
        )
        if self.managed_only:
            stmt = stmt.where(DatabaseTrigger.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        return [serialize_database_trigger(trigger, name) for trigger, name in result.all()]
