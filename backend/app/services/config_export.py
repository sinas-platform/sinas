"""
Configuration export service
Exports current database state to declarative YAML format
"""
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User, Group, GroupPermission
from app.models.llm_provider import LLMProvider
from app.models.mcp import MCPServer
from app.models.function import Function
from app.models.agent import Agent
from app.models.webhook import Webhook
from app.models.schedule import ScheduledJob
from app.core.encryption import EncryptionService

import yaml
import logging

logger = logging.getLogger(__name__)


def _remove_none_values(d: Dict) -> Dict:
    """Remove None values from dictionary recursively"""
    if not isinstance(d, dict):
        return d
    return {k: _remove_none_values(v) if isinstance(v, dict) else v
            for k, v in d.items() if v is not None}


class ConfigExportService:
    """Service for exporting current state to YAML configuration"""

    def __init__(self, db: AsyncSession, include_secrets: bool = False, managed_only: bool = False):
        self.db = db
        self.include_secrets = include_secrets
        self.managed_only = managed_only

    async def export_config(self) -> str:
        """Export current configuration to YAML string"""
        config_dict = {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasConfig",
            "metadata": {
                "name": "exported-config",
                "description": "Exported from SINAS database"
            },
            "spec": {}
        }

        # Export all resource types
        config_dict["spec"]["groups"] = await self._export_groups()
        config_dict["spec"]["users"] = await self._export_users()
        config_dict["spec"]["llmProviders"] = await self._export_llm_providers()
        config_dict["spec"]["mcpServers"] = await self._export_mcp_servers()

        config_dict["spec"]["functions"] = await self._export_functions()
        config_dict["spec"]["agents"] = await self._export_agents()
        config_dict["spec"]["webhooks"] = await self._export_webhooks()
        config_dict["spec"]["schedules"] = await self._export_schedules()

        # Convert to YAML
        return yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    async def _export_groups(self) -> List[Dict]:
        """Export groups"""
        stmt = select(Group)
        if self.managed_only:
            stmt = stmt.where(Group.managed_by == "config")

        result = await self.db.execute(stmt)
        groups = result.scalars().all()

        exported = []
        for group in groups:
            group_dict = {
                "name": group.name,
                "description": group.description,
            }
            if group.email_domain:
                group_dict["emailDomain"] = group.email_domain

            # Export permissions
            perm_stmt = select(GroupPermission).where(GroupPermission.group_id == group.id)
            perm_result = await self.db.execute(perm_stmt)
            permissions = perm_result.scalars().all()
            if permissions:
                group_dict["permissions"] = [
                    {"key": p.permission_key, "value": p.permission_value}
                    for p in permissions
                ]

            exported.append(group_dict)

        return exported

    async def _export_users(self) -> List[Dict]:
        """Export users"""
        stmt = select(User)
        if self.managed_only:
            stmt = stmt.where(User.managed_by == "config")

        result = await self.db.execute(stmt)
        users = result.scalars().all()

        exported = []
        for user in users:
            # Get user groups
            from app.models.user import GroupMember
            member_stmt = select(GroupMember).where(GroupMember.user_id == user.id)
            member_result = await self.db.execute(member_stmt)
            memberships = member_result.scalars().all()

            group_stmt = select(Group).where(Group.id.in_([m.group_id for m in memberships]))
            group_result = await self.db.execute(group_stmt)
            groups = group_result.scalars().all()

            user_dict = {
                "email": user.email,
                "lastLoginAt": user.last_login_at.isoformat() if user.last_login_at else None,
                "groups": [g.name for g in groups]
            }

            exported.append(user_dict)

        return exported

    async def _export_llm_providers(self) -> List[Dict]:
        """Export LLM providers"""
        stmt = select(LLMProvider)
        if self.managed_only:
            stmt = stmt.where(LLMProvider.managed_by == "config")

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

    async def _export_mcp_servers(self) -> List[Dict]:
        """Export MCP servers"""
        stmt = select(MCPServer)
        if self.managed_only:
            stmt = stmt.where(MCPServer.managed_by == "config")

        result = await self.db.execute(stmt)
        servers = result.scalars().all()

        # Get default group (Admins) for servers without group_id
        default_group_stmt = select(Group).where(Group.name == "Admins")
        default_group_result = await self.db.execute(default_group_stmt)
        default_group = default_group_result.scalar_one_or_none()

        exported = []
        for server in servers:
            group = None
            if server.group_id:
                group_stmt = select(Group).where(Group.id == server.group_id)
                group_result = await self.db.execute(group_stmt)
                group = group_result.scalar_one_or_none()

            server_dict = {
                "name": server.name,
                "url": server.url,
                "protocol": server.protocol,
                "isActive": server.is_active,
                "groupName": group.name if group else (default_group.name if default_group else "Admins"),
            }

            # Include API key if exporting secrets
            if self.include_secrets and server.api_key:
                server_dict["apiKey"] = EncryptionService.decrypt(server.api_key)

            exported.append(_remove_none_values(server_dict))

        return exported

    async def _export_functions(self) -> List[Dict]:
        """Export functions"""
        stmt = select(Function)
        if self.managed_only:
            stmt = stmt.where(Function.managed_by == "config")

        result = await self.db.execute(stmt)
        functions = result.scalars().all()

        # Get default group (Users) for functions without group_id
        default_group_stmt = select(Group).where(Group.name == "Users")
        default_group_result = await self.db.execute(default_group_stmt)
        default_group = default_group_result.scalar_one_or_none()

        exported = []
        for func in functions:
            # Get group name
            group = None
            if func.group_id:
                group_stmt = select(Group).where(Group.id == func.group_id)
                group_result = await self.db.execute(group_stmt)
                group = group_result.scalar_one_or_none()

            func_dict = {
                "name": func.name,
                "namespace": func.namespace,
                "description": func.description,
                "code": func.code,
                "inputSchema": func.input_schema,
                "outputSchema": func.output_schema,
                "groupName": group.name if group else (default_group.name if default_group else "Users"),
                "requirements": func.requirements if func.requirements else None,
                "enabledNamespaces": func.enabled_namespaces if func.enabled_namespaces else None,
            }

            exported.append(_remove_none_values(func_dict))

        return exported

    async def _export_agents(self) -> List[Dict]:
        """Export agents"""
        stmt = select(Agent)
        if self.managed_only:
            stmt = stmt.where(Agent.managed_by == "config")

        result = await self.db.execute(stmt)
        agents = result.scalars().all()

        # Get default group (Users) for agents without group_id
        default_group_stmt = select(Group).where(Group.name == "Users")
        default_group_result = await self.db.execute(default_group_stmt)
        default_group = default_group_result.scalar_one_or_none()

        exported = []
        for agent in agents:
            # Get group and provider names
            group = None
            if agent.group_id:
                group_stmt = select(Group).where(Group.id == agent.group_id)
                group_result = await self.db.execute(group_stmt)
                group = group_result.scalar_one_or_none()

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
                "groupName": group.name if group else (default_group.name if default_group else "Users"),
                "llmProviderName": provider.name if provider else None,
                "temperature": agent.temperature,
                "maxTokens": agent.max_tokens,
                "systemPrompt": agent.system_prompt,
                "enabledFunctions": agent.enabled_functions if agent.enabled_functions else None,
                "functionParameters": agent.function_parameters if agent.function_parameters else None,
                "enabledMcpTools": agent.enabled_mcp_tools if agent.enabled_mcp_tools else None,
                "enabledAgents": agent.enabled_agents if agent.enabled_agents else None,
                "stateNamespacesReadonly": agent.state_namespaces_readonly if agent.state_namespaces_readonly else None,
                "stateNamespacesReadwrite": agent.state_namespaces_readwrite if agent.state_namespaces_readwrite else None,
            }

            exported.append(_remove_none_values(agent_dict))

        return exported

    async def _export_webhooks(self) -> List[Dict]:
        """Export webhooks"""
        stmt = select(Webhook)
        if self.managed_only:
            stmt = stmt.where(Webhook.managed_by == "config")

        result = await self.db.execute(stmt)
        webhooks = result.scalars().all()

        # Get default group (Users) for webhooks without group_id
        default_group_stmt = select(Group).where(Group.name == "Users")
        default_group_result = await self.db.execute(default_group_stmt)
        default_group = default_group_result.scalar_one_or_none()

        exported = []
        for webhook in webhooks:
            # Get function and group names
            func_stmt = select(Function).where(Function.id == webhook.function_id)
            func_result = await self.db.execute(func_stmt)
            function = func_result.scalar_one_or_none()

            group = None
            if webhook.group_id:
                group_stmt = select(Group).where(Group.id == webhook.group_id)
                group_result = await self.db.execute(group_stmt)
                group = group_result.scalar_one_or_none()

            if function:
                webhook_dict = {
                    "path": webhook.path,
                    "functionNamespace": function.namespace,
                    "functionName": function.name,
                    "httpMethod": webhook.http_method,
                    "requiresAuth": webhook.requires_auth,
                    "description": webhook.description,
                    "groupName": group.name if group else (default_group.name if default_group else "Users"),
                    "defaultValues": webhook.default_values,
                }

                exported.append(_remove_none_values(webhook_dict))

        return exported

    async def _export_schedules(self) -> List[Dict]:
        """Export scheduled jobs"""
        stmt = select(ScheduledJob)
        if self.managed_only:
            stmt = stmt.where(ScheduledJob.managed_by == "config")

        result = await self.db.execute(stmt)
        schedules = result.scalars().all()

        # Get default group (Users) for schedules without group_id
        default_group_stmt = select(Group).where(Group.name == "Users")
        default_group_result = await self.db.execute(default_group_stmt)
        default_group = default_group_result.scalar_one_or_none()

        exported = []
        for schedule in schedules:
            # Get function and group names
            func_stmt = select(Function).where(Function.id == schedule.function_id)
            func_result = await self.db.execute(func_stmt)
            function = func_result.scalar_one_or_none()

            group = None
            if schedule.group_id:
                group_stmt = select(Group).where(Group.id == schedule.group_id)
                group_result = await self.db.execute(group_stmt)
                group = group_result.scalar_one_or_none()

            if function:
                schedule_dict = {
                    "name": schedule.name,
                    "functionNamespace": function.namespace,
                    "functionName": function.name,
                    "cronExpression": schedule.cron_expression,
                    "isActive": schedule.is_active,
                    "timezone": schedule.timezone,
                    "groupName": group.name if group else (default_group.name if default_group else "Users"),
                    "inputData": schedule.input_data,
                }

                exported.append(_remove_none_values(schedule_dict))

        return exported
