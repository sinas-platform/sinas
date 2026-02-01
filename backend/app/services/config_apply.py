"""
Configuration apply service
Handles idempotent application of declarative configuration
"""
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime

from app.models.user import User, Role, UserRole, RolePermission
from app.models.llm_provider import LLMProvider
from app.models.mcp import MCPServer
from app.models.function import Function, FunctionVersion
from app.models.agent import Agent
from app.models.webhook import Webhook
from app.models.schedule import ScheduledJob

from app.schemas.config import (
    SinasConfig,
    ConfigApplyResponse,
    ConfigApplySummary,
    ResourceChange,
)
from app.core.encryption import EncryptionService

import logging

logger = logging.getLogger(__name__)


class ConfigApplyService:
    """Service for applying declarative configuration"""

    def __init__(self, db: AsyncSession, config_name: str):
        self.db = db
        self.config_name = config_name
        self.summary = ConfigApplySummary()
        self.changes: List[ResourceChange] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []

        # Resource lookup caches (name -> id)
        self.group_ids: Dict[str, str] = {}
        self.user_ids: Dict[str, str] = {}
        self.datasource_ids: Dict[str, str] = {}
        self.function_ids: Dict[str, str] = {}
        self.agent_ids: Dict[str, str] = {}
        self.llm_provider_ids: Dict[str, str] = {}
        self.webhook_ids: Dict[str, str] = {}
        self.collection_ids: Dict[str, str] = {}
        self.folder_ids: Dict[str, str] = {}  # Alias for collection_ids

    def _calculate_hash(self, data: Dict[str, Any]) -> str:
        """Calculate hash for change detection"""
        # Create stable JSON string and hash it
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _track_change(self, action: str, resource_type: str, resource_name: str,
                      details: Optional[str] = None, changes: Optional[Dict[str, Any]] = None):
        """Track a resource change"""
        self.changes.append(ResourceChange(
            action=action,
            resourceType=resource_type,
            resourceName=resource_name,
            details=details,
            changes=changes
        ))

        # Update summary - map action to summary field name
        action_field_map = {
            "create": "created",
            "update": "updated",
            "unchanged": "unchanged",
            "delete": "deleted"
        }
        summary_field = action_field_map.get(action, action)
        summary_dict = getattr(self.summary, summary_field)
        summary_dict[resource_type] = summary_dict.get(resource_type, 0) + 1

    async def apply_config(self, config: SinasConfig, dry_run: bool = False) -> ConfigApplyResponse:
        """
        Apply configuration idempotently

        Args:
            config: Validated configuration
            dry_run: If True, don't actually apply changes

        Returns:
            ConfigApplyResponse with results
        """
        try:
            # Apply resources in dependency order
            await self._apply_groups(config.spec.groups, dry_run)
            await self._apply_users(config.spec.users, dry_run)
            await self._apply_llm_providers(config.spec.llmProviders, dry_run)
            await self._apply_mcp_servers(config.spec.mcpServers, dry_run)

            await self._apply_functions(config.spec.functions, dry_run)
            await self._apply_agents(config.spec.agents, dry_run)
            await self._apply_webhooks(config.spec.webhooks, dry_run)
            await self._apply_schedules(config.spec.schedules, dry_run)

            if not dry_run:
                await self.db.commit()

            return ConfigApplyResponse(
                success=True,
                summary=self.summary,
                changes=self.changes,
                errors=self.errors,
                warnings=self.warnings
            )

        except Exception as e:
            logger.error(f"Error applying config: {str(e)}", exc_info=True)
            await self.db.rollback()
            return ConfigApplyResponse(
                success=False,
                summary=self.summary,
                changes=self.changes,
                errors=[f"Fatal error: {str(e)}"],
                warnings=self.warnings
            )

    async def _apply_groups(self, groups, dry_run: bool):
        """Apply group configurations"""
        for group_config in groups:
            try:
                # Check if exists
                stmt = select(Role).where(Role.name == group_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Calculate hash
                config_hash = self._calculate_hash({
                    "name": group_config.name,
                    "description": group_config.description,
                    "email_domain": group_config.emailDomain,
                })

                if existing:
                    # Check if config-managed
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Group '{group_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "groups", group_config.name)
                        self.group_ids[group_config.name] = str(existing.id)
                        continue

                    # Check if changed
                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "groups", group_config.name)
                        self.group_ids[group_config.name] = str(existing.id)
                        continue

                    # Update
                    if not dry_run:
                        existing.description = group_config.description
                        existing.email_domain = group_config.emailDomain
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "groups", group_config.name,
                                       details="Updated group configuration")
                    self.group_ids[group_config.name] = str(existing.id)

                else:
                    # Create new
                    if not dry_run:
                        new_group = Group(
                            name=group_config.name,
                            description=group_config.description,
                            email_domain=group_config.emailDomain,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_group)
                        await self.db.flush()
                        self.group_ids[group_config.name] = str(new_group.id)
                    else:
                        self.group_ids[group_config.name] = "dry-run-id"

                    self._track_change("create", "groups", group_config.name,
                                       details="Created new group")

                # Apply permissions
                if not dry_run and group_config.permissions:
                    await self._apply_group_permissions(
                        self.group_ids[group_config.name],
                        group_config.permissions
                    )

            except Exception as e:
                self.errors.append(f"Error applying group '{group_config.name}': {str(e)}")

    async def _apply_group_permissions(self, group_id: str, permissions):
        """Apply permissions to a group"""
        # Delete existing config-managed permissions
        from sqlalchemy import delete
        stmt = delete(RolePermission).where(
            and_(
                RolePermission.group_id == group_id,
                RolePermission.managed_by == "config"
            )
        )
        await self.db.execute(stmt)

        # Add new permissions
        for perm in permissions:
            perm_obj = RolePermission(
                group_id=group_id,
                permission_key=perm.key,
                permission_value=perm.value,
                managed_by="config",
                config_name=self.config_name,
            )
            self.db.add(perm_obj)

    async def _apply_users(self, users, dry_run: bool):
        """Apply user configurations"""
        for user_config in users:
            try:
                stmt = select(User).where(User.email == user_config.email)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash({
                    "email": user_config.email,
                    "is_active": user_config.isActive,
                    "groups": sorted(user_config.groups),
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"User '{user_config.email}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "users", user_config.email)
                        self.user_ids[user_config.email] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "users", user_config.email)
                        self.user_ids[user_config.email] = str(existing.id)
                        continue

                    if not dry_run:
                        existing.is_active = user_config.isActive
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "users", user_config.email)
                    self.user_ids[user_config.email] = str(existing.id)

                else:
                    if not dry_run:
                        new_user = User(
                            email=user_config.email,
                            is_active=user_config.isActive,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_user)
                        await self.db.flush()
                        self.user_ids[user_config.email] = str(new_user.id)
                    else:
                        self.user_ids[user_config.email] = "dry-run-id"

                    self._track_change("create", "users", user_config.email)

                # Apply group memberships
                if not dry_run and user_config.groups:
                    await self._apply_user_groups(
                        self.user_ids[user_config.email],
                        user_config.groups
                    )

            except Exception as e:
                self.errors.append(f"Error applying user '{user_config.email}': {str(e)}")

    async def _apply_user_groups(self, user_id: str, group_names: List[str]):
        """Apply group memberships to a user"""
        # Remove existing config-managed memberships
        from sqlalchemy import delete
        stmt = delete(UserRole).where(
            and_(
                UserRole.user_id == user_id,
                UserRole.managed_by == "config"
            )
        )
        await self.db.execute(stmt)

        # Add new memberships
        for group_name in group_names:
            if group_name not in self.group_ids:
                self.warnings.append(f"Group '{group_name}' not found for user membership")
                continue

            membership = UserRole(
                user_id=user_id,
                group_id=self.group_ids[group_name],
                role="member",
                is_active=True,
                managed_by="config",
                config_name=self.config_name,
            )
            self.db.add(membership)

    async def _apply_llm_providers(self, providers, dry_run: bool):
        """Apply LLM provider configurations"""
        for provider_config in providers:
            try:
                stmt = select(LLMProvider).where(LLMProvider.name == provider_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Don't include API key in hash (it's encrypted)
                config_hash = self._calculate_hash({
                    "name": provider_config.name,
                    "type": provider_config.type,
                    "endpoint": provider_config.endpoint,
                    "models": sorted(provider_config.models),
                    "is_active": provider_config.isActive,
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"LLM provider '{provider_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "llmProviders", provider_config.name)
                        self.llm_provider_ids[provider_config.name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "llmProviders", provider_config.name)
                        self.llm_provider_ids[provider_config.name] = str(existing.id)
                        continue

                    if not dry_run:
                        existing.provider_type = provider_config.type
                        existing.api_endpoint = provider_config.endpoint
                        existing.config = existing.config or {}
                        existing.config["models"] = provider_config.models
                        existing.is_active = provider_config.isActive
                        if provider_config.apiKey:
                            existing.api_key = EncryptionService.encrypt(provider_config.apiKey)
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "llmProviders", provider_config.name)
                    self.llm_provider_ids[provider_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        encrypted_key = None
                        if provider_config.apiKey:
                            encrypted_key = EncryptionService.encrypt(provider_config.apiKey)

                        new_provider = LLMProvider(
                            name=provider_config.name,
                            provider_type=provider_config.type,
                            api_key=encrypted_key,
                            api_endpoint=provider_config.endpoint,
                            config={"models": provider_config.models},
                            is_active=provider_config.isActive,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_provider)
                        await self.db.flush()
                        self.llm_provider_ids[provider_config.name] = str(new_provider.id)
                    else:
                        self.llm_provider_ids[provider_config.name] = "dry-run-id"

                    self._track_change("create", "llmProviders", provider_config.name)

            except Exception as e:
                self.errors.append(f"Error applying LLM provider '{provider_config.name}': {str(e)}")

    async def _apply_mcp_servers(self, servers, dry_run: bool):
        """Apply MCP server configurations"""
        for server_config in servers:
            try:
                stmt = select(MCPServer).where(MCPServer.name == server_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Get group ID
                group_id = self.group_ids.get(server_config.groupName)

                # Don't include API key in hash (it's encrypted)
                config_hash = self._calculate_hash({
                    "name": server_config.name,
                    "url": server_config.url,
                    "protocol": server_config.protocol,
                    "is_active": server_config.isActive,
                    "group_name": server_config.groupName,
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"MCP server '{server_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "mcpServers", server_config.name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "mcpServers", server_config.name)
                        continue

                    if not dry_run:
                        existing.url = server_config.url
                        existing.protocol = server_config.protocol
                        existing.is_active = server_config.isActive
                        existing.group_id = group_id
                        if server_config.apiKey:
                            existing.api_key = EncryptionService.encrypt(server_config.apiKey)
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "mcpServers", server_config.name)

                else:
                    if not dry_run:
                        encrypted_key = None
                        if server_config.apiKey:
                            encrypted_key = EncryptionService.encrypt(server_config.apiKey)

                        new_server = MCPServer(
                            name=server_config.name,
                            url=server_config.url,
                            protocol=server_config.protocol,
                            api_key=encrypted_key,
                            is_active=server_config.isActive,
                            group_id=group_id,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_server)
                        await self.db.flush()

                    self._track_change("create", "mcpServers", server_config.name)

            except Exception as e:
                self.errors.append(f"Error applying MCP server '{server_config.name}': {str(e)}")

    async def _apply_functions(self, functions, dry_run: bool):
        """Apply function configurations"""
        for func_config in functions:
            try:
                stmt = select(Function).where(Function.name == func_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash({
                    "name": func_config.name,
                    "description": func_config.description,
                    "code": func_config.code,
                    "input_schema": func_config.inputSchema,
                    "output_schema": func_config.outputSchema,
                    "requirements": sorted(func_config.requirements) if func_config.requirements else [],
                    "tags": sorted(func_config.tags) if func_config.tags else [],
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Function '{func_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "functions", func_config.name)
                        self.function_ids[func_config.name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "functions", func_config.name)
                        self.function_ids[func_config.name] = str(existing.id)
                        continue

                    if not dry_run:
                        # Update function
                        existing.description = func_config.description
                        existing.code = func_config.code
                        existing.input_schema = func_config.inputSchema
                        existing.output_schema = func_config.outputSchema
                        existing.requirements = func_config.requirements
                        existing.tags = func_config.tags
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                        # Create new version if code changed
                        version = FunctionVersion(
                            function_id=existing.id,
                            version_number=existing.current_version + 1,
                            code=func_config.code,
                            input_schema=func_config.inputSchema,
                            output_schema=func_config.outputSchema,
                            created_by=existing.created_by,
                        )
                        self.db.add(version)
                        existing.current_version += 1

                    self._track_change("update", "functions", func_config.name)
                    self.function_ids[func_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        # Get group for owner
                        group_id = self.group_ids.get(func_config.groupName)
                        if not group_id:
                            self.errors.append(f"Group '{func_config.groupName}' not found for function '{func_config.name}'")
                            continue

                        # Get a user from the group for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(f"No users in group '{func_config.groupName}' for function '{func_config.name}'")
                            continue

                        new_function = Function(
                            name=func_config.name,
                            description=func_config.description,
                            code=func_config.code,
                            input_schema=func_config.inputSchema,
                            output_schema=func_config.outputSchema,
                            requirements=func_config.requirements,
                            enabled_namespaces=func_config.enabledNamespaces,
                            tags=func_config.tags,
                            created_by=member.user_id,
                            group_id=group_id,
                            current_version=1,
                            is_active=True,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_function)
                        await self.db.flush()

                        # Create initial version
                        version = FunctionVersion(
                            function_id=new_function.id,
                            version_number=1,
                            code=func_config.code,
                            input_schema=func_config.inputSchema,
                            output_schema=func_config.outputSchema,
                            created_by=member.user_id,
                        )
                        self.db.add(version)
                        self.function_ids[func_config.name] = str(new_function.id)
                    else:
                        self.function_ids[func_config.name] = "dry-run-id"

                    self._track_change("create", "functions", func_config.name)

            except Exception as e:
                self.errors.append(f"Error applying function '{func_config.name}': {str(e)}")

    def _normalize_function_references(self, function_names: List[str]) -> List[str]:
        """
        Normalize function names to namespace/name format.
        If a function name doesn't contain '/', prepend 'default/' to it.

        Args:
            function_names: List of function names (may or may not include namespace)

        Returns:
            List of normalized function names in namespace/name format
        """
        normalized = []
        for func_name in function_names:
            if "/" not in func_name:
                # No namespace specified, use default
                normalized.append(f"default/{func_name}")
            else:
                # Already has namespace
                normalized.append(func_name)
        return normalized

    async def _apply_agents(self, agents, dry_run: bool):
        """Apply agent configurations"""
        for agent_config in agents:
            try:
                stmt = select(Agent).where(
                    Agent.namespace == agent_config.namespace,
                    Agent.name == agent_config.name
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Normalize function references to namespace/name format
                normalized_functions = self._normalize_function_references(
                    agent_config.enabledFunctions
                ) if agent_config.enabledFunctions else []

                config_hash = self._calculate_hash({
                    "namespace": agent_config.namespace,
                    "name": agent_config.name,
                    "description": agent_config.description,
                    "llm_provider": agent_config.llmProviderName,
                    "model": agent_config.model,
                    "temperature": agent_config.temperature,
                    "max_tokens": agent_config.maxTokens,
                    "system_prompt": agent_config.systemPrompt,
                    "enabled_functions": sorted(normalized_functions),
                    "function_parameters": agent_config.functionParameters if agent_config.functionParameters else {},
                    "enabled_mcp_tools": sorted(agent_config.enabledMcpTools) if agent_config.enabledMcpTools else [],
                    "enabled_agents": sorted(agent_config.enabledAgents) if agent_config.enabledAgents else [],
                    "state_namespaces_readonly": sorted(agent_config.stateNamespacesReadonly) if agent_config.stateNamespacesReadonly else [],
                    "state_namespaces_readwrite": sorted(agent_config.stateNamespacesReadwrite) if agent_config.stateNamespacesReadwrite else [],
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Agent '{agent_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "agents", agent_config.name)
                        self.agent_ids[agent_config.name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "agents", agent_config.name)
                        self.agent_ids[agent_config.name] = str(existing.id)
                        continue

                    if not dry_run:
                        # Get LLM provider ID (None if not specified = use default)
                        llm_provider_id = None
                        if agent_config.llmProviderName:
                            llm_provider_id = self.llm_provider_ids.get(agent_config.llmProviderName)
                        existing.llm_provider_id = llm_provider_id

                        existing.description = agent_config.description
                        existing.model = agent_config.model
                        existing.temperature = agent_config.temperature
                        existing.max_tokens = agent_config.maxTokens
                        existing.system_prompt = agent_config.systemPrompt
                        existing.enabled_functions = normalized_functions
                        existing.function_parameters = agent_config.functionParameters
                        existing.enabled_mcp_tools = agent_config.enabledMcpTools
                        existing.enabled_agents = agent_config.enabledAgents
                        existing.state_namespaces_readonly = agent_config.stateNamespacesReadonly
                        existing.state_namespaces_readwrite = agent_config.stateNamespacesReadwrite
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "agents", agent_config.name)
                    self.agent_ids[agent_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        group_id = self.group_ids.get(agent_config.groupName)
                        if not group_id:
                            self.errors.append(f"Group '{agent_config.groupName}' not found for agent '{agent_config.name}'")
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(f"No users in group '{agent_config.groupName}' for agent '{agent_config.name}'")
                            continue

                        # Get LLM provider ID (None if not specified = use default)
                        llm_provider_id = None
                        if agent_config.llmProviderName:
                            llm_provider_id = self.llm_provider_ids.get(agent_config.llmProviderName)

                        new_agent = Agent(
                            namespace=agent_config.namespace,
                            name=agent_config.name,
                            description=agent_config.description,
                            llm_provider_id=llm_provider_id,
                            model=agent_config.model,
                            temperature=agent_config.temperature,
                            max_tokens=agent_config.maxTokens,
                            system_prompt=agent_config.systemPrompt,
                            enabled_functions=normalized_functions,
                            function_parameters=agent_config.functionParameters,
                            enabled_mcp_tools=agent_config.enabledMcpTools,
                            enabled_agents=agent_config.enabledAgents,
                            state_namespaces_readonly=agent_config.stateNamespacesReadonly,
                            state_namespaces_readwrite=agent_config.stateNamespacesReadwrite,
                            user_id=member.user_id,
                            group_id=group_id,
                            is_active=True,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_agent)
                        await self.db.flush()
                        self.agent_ids[agent_config.name] = str(new_agent.id)
                    else:
                        self.agent_ids[agent_config.name] = "dry-run-id"

                    self._track_change("create", "agents", agent_config.name)

            except Exception as e:
                self.errors.append(f"Error applying agent '{agent_config.name}': {str(e)}")

    async def _apply_webhooks(self, webhooks, dry_run: bool):
        """Apply webhook configurations"""
        for webhook_config in webhooks:
            try:
                stmt = select(Webhook).where(Webhook.path == webhook_config.path)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash({
                    "path": webhook_config.path,
                    "function_name": webhook_config.functionName,
                    "http_method": webhook_config.httpMethod,
                    "description": webhook_config.description,
                    "requires_auth": webhook_config.requiresAuth,
                    "default_values": webhook_config.defaultValues,
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Webhook '{webhook_config.path}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "webhooks", webhook_config.path)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "webhooks", webhook_config.path)
                        continue

                    if not dry_run:
                        function_id = self.function_ids.get(webhook_config.functionName)
                        if function_id:
                            existing.function_id = function_id

                        existing.http_method = webhook_config.httpMethod
                        existing.description = webhook_config.description
                        existing.requires_auth = webhook_config.requiresAuth
                        existing.default_values = webhook_config.defaultValues
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "webhooks", webhook_config.path)

                else:
                    if not dry_run:
                        function_id = self.function_ids.get(webhook_config.functionName)
                        if not function_id:
                            self.errors.append(f"Function '{webhook_config.functionName}' not found for webhook '{webhook_config.path}'")
                            continue

                        group_id = self.group_ids.get(webhook_config.groupName)
                        if not group_id:
                            self.errors.append(f"Group '{webhook_config.groupName}' not found for webhook '{webhook_config.path}'")
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(f"No users in group '{webhook_config.groupName}' for webhook '{webhook_config.path}'")
                            continue

                        new_webhook = Webhook(
                            path=webhook_config.path,
                            function_id=function_id,
                            http_method=webhook_config.httpMethod,
                            description=webhook_config.description,
                            requires_auth=webhook_config.requiresAuth,
                            default_values=webhook_config.defaultValues,
                            created_by=member.user_id,
                            group_id=group_id,
                            is_active=True,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_webhook)

                    self._track_change("create", "webhooks", webhook_config.path)

            except Exception as e:
                self.errors.append(f"Error applying webhook '{webhook_config.path}': {str(e)}")


    async def _apply_schedules(self, schedules, dry_run: bool):
        """Apply schedule configurations"""
        for schedule_config in schedules:
            try:
                stmt = select(ScheduledJob).where(ScheduledJob.name == schedule_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash({
                    "name": schedule_config.name,
                    "function_name": schedule_config.functionName,
                    "cron_expression": schedule_config.cronExpression,
                    "timezone": schedule_config.timezone,
                    "input_data": schedule_config.inputData,
                    "is_active": schedule_config.isActive,
                })

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Schedule '{schedule_config.name}' exists but is not config-managed. Skipping."
                        )
                        self._track_change("unchanged", "schedules", schedule_config.name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "schedules", schedule_config.name)
                        continue

                    if not dry_run:
                        function_id = self.function_ids.get(schedule_config.functionName)
                        if function_id:
                            existing.function_id = function_id

                        existing.cron_expression = schedule_config.cronExpression
                        existing.timezone = schedule_config.timezone
                        existing.input_data = schedule_config.inputData
                        existing.is_active = schedule_config.isActive
                        existing.config_checksum = config_hash

                    self._track_change("update", "schedules", schedule_config.name)

                else:
                    if not dry_run:
                        function_id = self.function_ids.get(schedule_config.functionName)
                        if not function_id:
                            self.errors.append(f"Function '{schedule_config.functionName}' not found for schedule '{schedule_config.name}'")
                            continue

                        group_id = self.group_ids.get(schedule_config.groupName)
                        if not group_id:
                            self.errors.append(f"Group '{schedule_config.groupName}' not found for schedule '{schedule_config.name}'")
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(f"No users in group '{schedule_config.groupName}' for schedule '{schedule_config.name}'")
                            continue

                        new_schedule = ScheduledJob(
                            name=schedule_config.name,
                            function_id=function_id,
                            cron_expression=schedule_config.cronExpression,
                            timezone=schedule_config.timezone,
                            input_data=schedule_config.inputData,
                            is_active=schedule_config.isActive,
                            created_by=member.user_id,
                            group_id=group_id,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_schedule)

                    self._track_change("create", "schedules", schedule_config.name)

            except Exception as e:
                self.errors.append(f"Error applying schedule '{schedule_config.name}': {str(e)}")



