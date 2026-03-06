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
from app.models.function import Function
from app.models.llm_provider import LLMProvider

from app.models.database_connection import DatabaseConnection
from app.models.database_trigger import DatabaseTrigger
from app.models.schedule import ScheduledJob
from app.models.template import Template
from app.models.user import Role, RolePermission, User
from app.models.webhook import Webhook

logger = logging.getLogger(__name__)


def _remove_none_values(d: dict) -> dict:
    """Remove None values from dictionary recursively"""
    if not isinstance(d, dict):
        return d
    return {
        k: _remove_none_values(v) if isinstance(v, dict) else v
        for k, v in d.items()
        if v is not None
    }


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


        config_dict["spec"]["functions"] = await self._export_functions()
        config_dict["spec"]["templates"] = await self._export_templates()
        config_dict["spec"]["stores"] = await self._export_stores()
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
        stmt = select(LLMProvider)
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

    async def _export_functions(self) -> list[dict]:
        """Export functions"""
        stmt = select(Function)
        if self.managed_only:
            stmt = stmt.where(Function.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        functions = result.scalars().all()

        exported = []
        for func in functions:
            func_dict = {
                "name": func.name,
                "namespace": func.namespace,
                "description": func.description,
                "code": func.code,
                "inputSchema": func.input_schema,
                "outputSchema": func.output_schema,
                "requirements": func.requirements if func.requirements else None,
                "enabledNamespaces": func.enabled_namespaces if func.enabled_namespaces else None,
                "icon": func.icon,
            }

            exported.append(_remove_none_values(func_dict))

        return exported

    async def _export_agents(self) -> list[dict]:
        """Export agents"""
        stmt = select(Agent)
        if self.managed_only:
            stmt = stmt.where(Agent.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        agents = result.scalars().all()

        exported = []
        for agent in agents:
            # Get provider name
            provider = None
            if agent.llm_provider_id:
                provider_stmt = select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
                provider_result = await self.db.execute(provider_stmt)
                provider = provider_result.scalar_one_or_none()

            agent_dict = {
                "name": agent.name,
                "namespace": agent.namespace,
                "description": agent.description,
                "model": agent.model,
                "llmProviderName": provider.name if provider else None,
                "temperature": agent.temperature,
                "maxTokens": agent.max_tokens,
                "systemPrompt": agent.system_prompt,
                "enabledFunctions": agent.enabled_functions if agent.enabled_functions else None,
                "functionParameters": agent.function_parameters
                if agent.function_parameters
                else None,
                "statusTemplates": agent.status_templates
                if agent.status_templates
                else None,
                "enabledAgents": agent.enabled_agents if agent.enabled_agents else None,
                "enabledStores": agent.enabled_stores
                if agent.enabled_stores
                else None,
                "icon": agent.icon,
            }

            exported.append(_remove_none_values(agent_dict))

        return exported

    async def _export_stores(self) -> list[dict]:
        """Export stores"""
        from app.models.store import Store
        stmt = select(Store)
        if self.managed_only:
            stmt = stmt.where(Store.managed_by == self.managed_by)
        result = await self.db.execute(stmt)
        stores = result.scalars().all()
        exported = []
        for store in stores:
            store_dict = {
                "namespace": store.namespace,
                "name": store.name,
                "description": store.description,
                "strict": store.strict,
                "defaultVisibility": store.default_visibility,
                "encrypted": store.encrypted,
            }
            if store.schema:
                store_dict["schema"] = store.schema
            exported.append(_remove_none_values(store_dict))
        return exported

    async def _export_webhooks(self) -> list[dict]:
        """Export webhooks"""
        stmt = select(Webhook)
        if self.managed_only:
            stmt = stmt.where(Webhook.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        webhooks = result.scalars().all()

        exported = []
        for webhook in webhooks:
            webhook_dict = {
                "path": webhook.path,
                "functionName": f"{webhook.function_namespace}/{webhook.function_name}",
                "httpMethod": webhook.http_method,
                "requiresAuth": webhook.requires_auth,
                "description": webhook.description,
                "defaultValues": webhook.default_values,
            }

            exported.append(_remove_none_values(webhook_dict))

        return exported

    async def _export_templates(self) -> list[dict]:
        """Export templates"""
        stmt = select(Template)
        if self.managed_only:
            stmt = stmt.where(Template.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        templates = result.scalars().all()

        exported = []
        for tmpl in templates:
            tmpl_dict = {
                "namespace": tmpl.namespace,
                "name": tmpl.name,
                "description": tmpl.description,
                "title": tmpl.title,
                "htmlContent": tmpl.html_content,
                "textContent": tmpl.text_content,
                "variableSchema": tmpl.variable_schema if tmpl.variable_schema else None,
            }
            exported.append(_remove_none_values(tmpl_dict))

        return exported

    async def _export_schedules(self) -> list[dict]:
        """Export scheduled jobs"""
        stmt = select(ScheduledJob)
        if self.managed_only:
            stmt = stmt.where(ScheduledJob.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        schedules = result.scalars().all()

        exported = []
        for schedule in schedules:
            schedule_dict = {
                "name": schedule.name,
                "scheduleType": schedule.schedule_type,
                "functionName": f"{schedule.target_namespace}/{schedule.target_name}"
                if schedule.schedule_type == "function"
                else None,
                "agentName": f"{schedule.target_namespace}/{schedule.target_name}"
                if schedule.schedule_type == "agent"
                else None,
                "content": schedule.content,
                "cronExpression": schedule.cron_expression,
                "isActive": schedule.is_active,
                "timezone": schedule.timezone,
                "inputData": schedule.input_data,
            }

            exported.append(_remove_none_values(schedule_dict))

        return exported

    async def _export_database_triggers(self) -> list[dict]:
        """Export database triggers"""
        stmt = select(DatabaseTrigger)
        if self.managed_only:
            stmt = stmt.where(DatabaseTrigger.managed_by == self.managed_by)

        result = await self.db.execute(stmt)
        triggers = result.scalars().all()

        exported = []
        for trigger in triggers:
            # Resolve connection id -> name
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

            trigger_dict = {
                "name": trigger.name,
                "connectionName": conn_name,
                "schemaName": trigger.schema_name,
                "tableName": trigger.table_name,
                "operations": trigger.operations,
                "functionName": f"{trigger.function_namespace}/{trigger.function_name}",
                "pollColumn": trigger.poll_column,
                "pollIntervalSeconds": trigger.poll_interval_seconds,
                "batchSize": trigger.batch_size,
                "isActive": trigger.is_active,
            }

            exported.append(_remove_none_values(trigger_dict))

        return exported
