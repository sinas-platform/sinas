"""
Configuration apply service
Handles idempotent application of declarative configuration
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import EncryptionService
from app.models.agent import Agent
from app.models.app import App
from app.models.file import Collection
from app.models.function import Function, FunctionVersion
from app.models.database_connection import DatabaseConnection
from app.models.llm_provider import LLMProvider
from app.models.query import Query
from app.models.database_trigger import DatabaseTrigger
from app.models.schedule import ScheduledJob
from app.models.component import Component
from app.models.skill import Skill
from app.models.store import Store
from app.models.template import Template
from app.models.user import Role, RolePermission, User, UserRole
from app.models.webhook import Webhook
from app.schemas.config import (
    ConfigApplyResponse,
    ConfigApplySummary,
    ResourceChange,
    SinasConfig,
)

logger = logging.getLogger(__name__)


class ConfigApplyService:
    """Service for applying declarative configuration"""

    def __init__(
        self,
        db: AsyncSession,
        config_name: str,
        owner_user_id: str,
        managed_by: str = "config",
        auto_commit: bool = True,
        skip_resource_types: Optional[set[str]] = None,
    ):
        self.db = db
        self.config_name = config_name
        self.owner_user_id = owner_user_id
        self.managed_by = managed_by
        self.auto_commit = auto_commit
        self.skip_resource_types = skip_resource_types or set()
        self.summary = ConfigApplySummary()
        self.changes: list[ResourceChange] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

        # Resource lookup caches (name -> id)
        self.role_ids: dict[str, str] = {}
        self.user_ids: dict[str, str] = {}
        self.datasource_ids: dict[str, str] = {}
        self.function_ids: dict[str, str] = {}
        self.agent_ids: dict[str, str] = {}
        self.llm_provider_ids: dict[str, str] = {}
        self.database_connection_ids: dict[str, str] = {}
        self.webhook_ids: dict[str, str] = {}
        self.collection_ids: dict[str, str] = {}
        self.store_ids: dict[str, str] = {}
        self.folder_ids: dict[str, str] = {}  # Alias for collection_ids

    def _calculate_hash(self, data: dict[str, Any]) -> str:
        """Calculate hash for change detection"""
        # Create stable JSON string and hash it
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()

    def _track_change(
        self,
        action: str,
        resource_type: str,
        resource_name: str,
        details: Optional[str] = None,
        changes: Optional[dict[str, Any]] = None,
    ):
        """Track a resource change"""
        self.changes.append(
            ResourceChange(
                action=action,
                resourceType=resource_type,
                resourceName=resource_name,
                details=details,
                changes=changes,
            )
        )

        # Update summary - map action to summary field name
        action_field_map = {
            "create": "created",
            "update": "updated",
            "unchanged": "unchanged",
            "delete": "deleted",
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
            if "roles" not in self.skip_resource_types:
                await self._apply_roles(config.spec.roles, dry_run)
            if "users" not in self.skip_resource_types:
                await self._apply_users(config.spec.users, dry_run)
            if "llmProviders" not in self.skip_resource_types:
                await self._apply_llm_providers(config.spec.llmProviders, dry_run)
            if "databaseConnections" not in self.skip_resource_types:
                await self._apply_database_connections(config.spec.databaseConnections, dry_run)

            if "functions" not in self.skip_resource_types:
                await self._apply_functions(config.spec.functions, dry_run)
            if "skills" not in self.skip_resource_types:
                await self._apply_skills(config.spec.skills, dry_run)
            if "components" not in self.skip_resource_types:
                await self._apply_components(config.spec.components, dry_run)
            if "queries" not in self.skip_resource_types:
                await self._apply_queries(config.spec.queries, dry_run)
            if "collections" not in self.skip_resource_types:
                await self._apply_collections(config.spec.collections, dry_run)
            if "templates" not in self.skip_resource_types:
                await self._apply_templates(config.spec.templates, dry_run)
            if "stores" not in self.skip_resource_types:
                await self._apply_stores(config.spec.stores, dry_run)
            if "apps" not in self.skip_resource_types:
                await self._apply_apps(config.spec.apps, dry_run)
            if "agents" not in self.skip_resource_types:
                await self._apply_agents(config.spec.agents, dry_run)
            if "webhooks" not in self.skip_resource_types:
                await self._apply_webhooks(config.spec.webhooks, dry_run)
            if "schedules" not in self.skip_resource_types:
                await self._apply_schedules(config.spec.schedules, dry_run)
            if "databaseTriggers" not in self.skip_resource_types:
                await self._apply_database_triggers(config.spec.databaseTriggers, dry_run)

            if not dry_run and self.auto_commit:
                await self.db.commit()

            return ConfigApplyResponse(
                success=True,
                summary=self.summary,
                changes=self.changes,
                errors=self.errors,
                warnings=self.warnings,
            )

        except Exception as e:
            logger.error(f"Error applying config: {str(e)}", exc_info=True)
            await self.db.rollback()
            return ConfigApplyResponse(
                success=False,
                summary=self.summary,
                changes=self.changes,
                errors=[f"Fatal error: {str(e)}"],
                warnings=self.warnings,
            )

    async def _apply_roles(self, roles, dry_run: bool):
        """Apply role configurations"""
        for role_config in roles:
            try:
                # Check if exists
                stmt = select(Role).where(Role.name == role_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Calculate hash
                config_hash = self._calculate_hash(
                    {
                        "name": role_config.name,
                        "description": role_config.description,
                        "email_domain": role_config.emailDomain,
                    }
                )

                if existing:
                    # Check if config-managed
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Role '{role_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "roles", role_config.name)
                        self.role_ids[role_config.name] = str(existing.id)
                        continue

                    # Check if changed
                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "roles", role_config.name)
                        self.role_ids[role_config.name] = str(existing.id)
                        continue

                    # Update
                    if not dry_run:
                        existing.description = role_config.description
                        existing.email_domain = role_config.emailDomain
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change(
                        "update", "roles", role_config.name, details="Updated role configuration"
                    )
                    self.role_ids[role_config.name] = str(existing.id)

                else:
                    # Create new
                    if not dry_run:
                        new_role = Role(
                            name=role_config.name,
                            description=role_config.description,
                            email_domain=role_config.emailDomain,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_role)
                        await self.db.flush()
                        self.role_ids[role_config.name] = str(new_role.id)
                    else:
                        self.role_ids[role_config.name] = "dry-run-id"

                    self._track_change(
                        "create", "roles", role_config.name, details="Created new role"
                    )

                # Apply permissions
                if not dry_run and role_config.permissions:
                    await self._apply_role_permissions(
                        self.role_ids[role_config.name], role_config.permissions
                    )

            except Exception as e:
                self.errors.append(f"Error applying role '{role_config.name}': {str(e)}")

    async def _apply_role_permissions(self, role_id: str, permissions):
        """Apply permissions to a role"""
        # Delete existing permissions for this role
        from sqlalchemy import delete

        stmt = delete(RolePermission).where(RolePermission.role_id == role_id)
        await self.db.execute(stmt)

        # Add new permissions
        for perm in permissions:
            perm_obj = RolePermission(
                role_id=role_id,
                permission_key=perm.key,
                permission_value=perm.value,
            )
            self.db.add(perm_obj)

    async def _apply_users(self, users, dry_run: bool):
        """Apply user configurations"""
        for user_config in users:
            try:
                stmt = select(User).where(User.email == user_config.email)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "email": user_config.email,
                        "is_active": user_config.isActive,
                        "roles": sorted(user_config.roles),
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"User '{user_config.email}' exists but is not managed by '{self.managed_by}'. Skipping."
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
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_user)
                        await self.db.flush()
                        self.user_ids[user_config.email] = str(new_user.id)
                    else:
                        self.user_ids[user_config.email] = "dry-run-id"

                    self._track_change("create", "users", user_config.email)

                # Apply role memberships
                if not dry_run and user_config.roles:
                    await self._apply_user_roles(
                        self.user_ids[user_config.email], user_config.roles
                    )

            except Exception as e:
                self.errors.append(f"Error applying user '{user_config.email}': {str(e)}")

    async def _apply_user_roles(self, user_id: str, role_names: list[str]):
        """Apply role memberships to a user"""
        # Remove existing memberships for this user
        from sqlalchemy import delete

        stmt = delete(UserRole).where(UserRole.user_id == user_id)
        await self.db.execute(stmt)

        # Add new memberships
        for role_name in role_names:
            if role_name not in self.role_ids:
                self.warnings.append(f"Role '{role_name}' not found for user membership")
                continue

            membership = UserRole(
                user_id=user_id,
                role_id=self.role_ids[role_name],
                active=True,
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
                config_hash = self._calculate_hash(
                    {
                        "name": provider_config.name,
                        "type": provider_config.type,
                        "endpoint": provider_config.endpoint,
                        "models": sorted(provider_config.models),
                        "is_active": provider_config.isActive,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"LLM provider '{provider_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
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
                            managed_by=self.managed_by,
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
                self.errors.append(
                    f"Error applying LLM provider '{provider_config.name}': {str(e)}"
                )

    async def _apply_database_connections(self, connections, dry_run: bool):
        """Apply database connection configurations"""
        for conn_config in connections:
            try:
                stmt = select(DatabaseConnection).where(
                    DatabaseConnection.name == conn_config.name
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Don't include password in hash (it's encrypted)
                config_hash = self._calculate_hash(
                    {
                        "name": conn_config.name,
                        "connection_type": conn_config.connectionType,
                        "host": conn_config.host,
                        "port": conn_config.port,
                        "database": conn_config.database,
                        "username": conn_config.username,
                        "ssl_mode": conn_config.sslMode,
                        "config": conn_config.config,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Database connection '{conn_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change(
                            "unchanged", "databaseConnections", conn_config.name
                        )
                        self.database_connection_ids[conn_config.name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change(
                            "unchanged", "databaseConnections", conn_config.name
                        )
                        self.database_connection_ids[conn_config.name] = str(existing.id)
                        continue

                    if not dry_run:
                        existing.connection_type = conn_config.connectionType
                        existing.host = conn_config.host
                        existing.port = conn_config.port
                        existing.database = conn_config.database
                        existing.username = conn_config.username
                        existing.ssl_mode = conn_config.sslMode
                        existing.config = conn_config.config
                        if conn_config.password:
                            existing.password = EncryptionService.encrypt(conn_config.password)
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "databaseConnections", conn_config.name)
                    self.database_connection_ids[conn_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        encrypted_password = None
                        if conn_config.password:
                            encrypted_password = EncryptionService.encrypt(
                                conn_config.password
                            )

                        new_conn = DatabaseConnection(
                            name=conn_config.name,
                            connection_type=conn_config.connectionType,
                            host=conn_config.host,
                            port=conn_config.port,
                            database=conn_config.database,
                            username=conn_config.username,
                            password=encrypted_password,
                            ssl_mode=conn_config.sslMode,
                            config=conn_config.config,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_conn)
                        await self.db.flush()
                        self.database_connection_ids[conn_config.name] = str(new_conn.id)
                    else:
                        self.database_connection_ids[conn_config.name] = "dry-run-id"

                    self._track_change("create", "databaseConnections", conn_config.name)

            except Exception as e:
                self.errors.append(
                    f"Error applying database connection '{conn_config.name}': {str(e)}"
                )

    async def _apply_queries(self, queries, dry_run: bool):
        """Apply query configurations"""
        for query_config in queries:
            resource_name = f"{query_config.namespace}/{query_config.name}"
            try:
                stmt = select(Query).where(
                    Query.namespace == query_config.namespace,
                    Query.name == query_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": query_config.namespace,
                        "name": query_config.name,
                        "description": query_config.description,
                        "connection_name": query_config.connectionName,
                        "operation": query_config.operation,
                        "sql": query_config.sql,
                        "input_schema": query_config.inputSchema,
                        "output_schema": query_config.outputSchema,
                        "timeout_ms": query_config.timeoutMs,
                        "max_rows": query_config.maxRows,
                    }
                )

                # Resolve database connection name to ID
                db_conn_id = self.database_connection_ids.get(query_config.connectionName)
                if not db_conn_id:
                    # Try loading from database
                    db_conn = await DatabaseConnection.get_by_name(
                        self.db, query_config.connectionName
                    )
                    if db_conn:
                        db_conn_id = str(db_conn.id)
                    else:
                        self.errors.append(
                            f"Database connection '{query_config.connectionName}' not found for query '{resource_name}'"
                        )
                        continue

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Query '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "queries", resource_name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "queries", resource_name)
                        continue

                    if not dry_run:
                        existing.description = query_config.description
                        existing.database_connection_id = db_conn_id
                        existing.operation = query_config.operation
                        existing.sql = query_config.sql
                        existing.input_schema = query_config.inputSchema or {}
                        existing.output_schema = query_config.outputSchema or {}
                        existing.timeout_ms = query_config.timeoutMs
                        existing.max_rows = query_config.maxRows
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "queries", resource_name)

                else:
                    if not dry_run:
                        new_query = Query(
                            namespace=query_config.namespace,
                            name=query_config.name,
                            description=query_config.description,
                            database_connection_id=db_conn_id,
                            operation=query_config.operation,
                            sql=query_config.sql,
                            input_schema=query_config.inputSchema or {},
                            output_schema=query_config.outputSchema or {},
                            timeout_ms=query_config.timeoutMs,
                            max_rows=query_config.maxRows,
                            user_id=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_query)

                    self._track_change("create", "queries", resource_name)

            except Exception as e:
                self.errors.append(
                    f"Error applying query '{resource_name}': {str(e)}"
                )

    async def _apply_functions(self, functions, dry_run: bool):
        """Apply function configurations"""
        for func_config in functions:
            try:
                stmt = select(Function).where(Function.name == func_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "name": func_config.name,
                        "description": func_config.description,
                        "code": func_config.code,
                        "input_schema": func_config.inputSchema,
                        "output_schema": func_config.outputSchema,
                        "requirements": sorted(func_config.requirements)
                        if func_config.requirements
                        else [],
                        "tags": sorted(func_config.tags) if func_config.tags else [],
                        "icon": func_config.icon,
                        "timeout": func_config.timeout,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Function '{func_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
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
                        existing.icon = func_config.icon
                        existing.timeout = func_config.timeout
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                        # Create new version if code changed
                        version = FunctionVersion(
                            function_id=existing.id,
                            version_number=existing.current_version + 1,
                            code=func_config.code,
                            input_schema=func_config.inputSchema,
                            output_schema=func_config.outputSchema,
                            created_by=existing.user_id,
                        )
                        self.db.add(version)
                        existing.current_version += 1

                    self._track_change("update", "functions", func_config.name)
                    self.function_ids[func_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        new_function = Function(
                            name=func_config.name,
                            description=func_config.description,
                            code=func_config.code,
                            input_schema=func_config.inputSchema,
                            output_schema=func_config.outputSchema,
                            requirements=func_config.requirements,
                            enabled_namespaces=func_config.enabledNamespaces,
                            tags=func_config.tags,
                            icon=func_config.icon,
                            timeout=func_config.timeout,
                            user_id=self.owner_user_id,
                            current_version=1,
                            is_active=True,
                            managed_by=self.managed_by,
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
                            created_by=self.owner_user_id,
                        )
                        self.db.add(version)
                        self.function_ids[func_config.name] = str(new_function.id)
                    else:
                        self.function_ids[func_config.name] = "dry-run-id"

                    self._track_change("create", "functions", func_config.name)

            except Exception as e:
                self.errors.append(f"Error applying function '{func_config.name}': {str(e)}")

    def _normalize_function_references(self, function_names: list[str]) -> list[str]:
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

    def _normalize_skill_references(self, skills: list[Any]) -> list[dict[str, Any]]:
        """
        Normalize skill references to dict format with skill and preload keys.
        Supports backward compatibility with string format.

        Args:
            skills: List of skill configs (strings or dicts)

        Returns:
            List of normalized skill configs as dicts
        """
        normalized = []
        for skill_item in skills:
            if isinstance(skill_item, str):
                # Old format: "namespace/name"
                skill_ref = skill_item
                if "/" not in skill_ref:
                    skill_ref = f"default/{skill_ref}"
                normalized.append({"skill": skill_ref, "preload": False})
            elif isinstance(skill_item, dict):
                # New format: {"skill": "namespace/name", "preload": bool}
                skill_ref = skill_item.get("skill", "")
                if "/" not in skill_ref:
                    skill_ref = f"default/{skill_ref}"
                normalized.append({"skill": skill_ref, "preload": skill_item.get("preload", False)})
            else:
                # Pydantic model (EnabledSkillConfigYaml)
                skill_ref = skill_item.skill
                if "/" not in skill_ref:
                    skill_ref = f"default/{skill_ref}"
                normalized.append({"skill": skill_ref, "preload": skill_item.preload})
        return normalized

    async def _apply_skills(self, skills, dry_run: bool):
        """Apply skill configurations"""
        for skill_config in skills:
            try:
                stmt = select(Skill).where(
                    Skill.namespace == skill_config.namespace, Skill.name == skill_config.name
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": skill_config.namespace,
                        "name": skill_config.name,
                        "description": skill_config.description,
                        "content": skill_config.content,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Skill '{skill_config.namespace}/{skill_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change(
                            "unchanged", "skills", f"{skill_config.namespace}/{skill_config.name}"
                        )
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change(
                            "unchanged", "skills", f"{skill_config.namespace}/{skill_config.name}"
                        )
                        continue

                    if not dry_run:
                        # Update skill
                        existing.description = skill_config.description
                        existing.content = skill_config.content
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change(
                        "update", "skills", f"{skill_config.namespace}/{skill_config.name}"
                    )

                else:
                    if not dry_run:
                        new_skill = Skill(
                            namespace=skill_config.namespace,
                            name=skill_config.name,
                            description=skill_config.description,
                            content=skill_config.content,
                            user_id=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_skill)

                    self._track_change(
                        "create", "skills", f"{skill_config.namespace}/{skill_config.name}"
                    )

            except Exception as e:
                self.errors.append(
                    f"Error applying skill '{skill_config.namespace}/{skill_config.name}': {str(e)}"
                )

    async def _apply_components(self, components, dry_run: bool):
        """Apply component configurations"""
        for comp_config in components:
            resource_name = f"{comp_config.namespace}/{comp_config.name}"
            try:
                stmt = select(Component).where(
                    Component.namespace == comp_config.namespace,
                    Component.name == comp_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": comp_config.namespace,
                        "name": comp_config.name,
                        "title": comp_config.title,
                        "description": comp_config.description,
                        "source_code": comp_config.sourceCode,
                        "input_schema": comp_config.inputSchema or {},
                        "enabled_agents": comp_config.enabledAgents,
                        "enabled_functions": comp_config.enabledFunctions,
                        "enabled_queries": comp_config.enabledQueries,
                        "enabled_components": comp_config.enabledComponents,
                        "enabled_stores": self._normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else [],
                        "css_overrides": comp_config.cssOverrides,
                        "visibility": comp_config.visibility,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Component '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "components", resource_name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "components", resource_name)
                        continue

                    if not dry_run:
                        source_changed = existing.source_code != comp_config.sourceCode
                        existing.title = comp_config.title
                        existing.description = comp_config.description
                        existing.source_code = comp_config.sourceCode
                        existing.input_schema = comp_config.inputSchema
                        existing.enabled_agents = comp_config.enabledAgents
                        existing.enabled_functions = comp_config.enabledFunctions
                        existing.enabled_queries = comp_config.enabledQueries
                        existing.enabled_components = comp_config.enabledComponents
                        existing.enabled_stores = self._normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else []
                        existing.css_overrides = comp_config.cssOverrides
                        existing.visibility = comp_config.visibility
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()
                        if source_changed:
                            existing.compile_status = "pending"
                            existing.compiled_bundle = None
                            existing.source_map = None
                            existing.compile_errors = None
                            existing.version += 1

                    self._track_change("update", "components", resource_name)

                else:
                    if not dry_run:
                        new_component = Component(
                            namespace=comp_config.namespace,
                            name=comp_config.name,
                            title=comp_config.title,
                            description=comp_config.description,
                            source_code=comp_config.sourceCode,
                            input_schema=comp_config.inputSchema,
                            enabled_agents=comp_config.enabledAgents,
                            enabled_functions=comp_config.enabledFunctions,
                            enabled_queries=comp_config.enabledQueries,
                            enabled_components=comp_config.enabledComponents,
                            enabled_stores=self._normalize_store_references(comp_config.enabledStores) if hasattr(comp_config, 'enabledStores') else [],
                            css_overrides=comp_config.cssOverrides,
                            visibility=comp_config.visibility,
                            user_id=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                            compile_status="pending",
                        )
                        self.db.add(new_component)

                    self._track_change("create", "components", resource_name)

            except Exception as e:
                self.errors.append(f"Error applying component '{resource_name}': {str(e)}")

    async def _apply_collections(self, collections, dry_run: bool):
        """Apply collection configurations"""
        for coll_config in collections:
            resource_name = f"{coll_config.namespace}/{coll_config.name}"
            try:
                stmt = select(Collection).where(
                    Collection.namespace == coll_config.namespace,
                    Collection.name == coll_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": coll_config.namespace,
                        "name": coll_config.name,
                        "metadata_schema": coll_config.metadataSchema or {},
                        "content_filter_function": coll_config.contentFilterFunction,
                        "post_upload_function": coll_config.postUploadFunction,
                        "max_file_size_mb": coll_config.maxFileSizeMb,
                        "max_total_size_gb": coll_config.maxTotalSizeGb,
                        "is_public": coll_config.isPublic,
                        "allow_shared_files": coll_config.allowSharedFiles,
                        "allow_private_files": coll_config.allowPrivateFiles,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Collection '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "collections", resource_name)
                        self.collection_ids[resource_name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "collections", resource_name)
                        self.collection_ids[resource_name] = str(existing.id)
                        continue

                    if not dry_run:
                        existing.metadata_schema = coll_config.metadataSchema or {}
                        existing.content_filter_function = coll_config.contentFilterFunction
                        existing.post_upload_function = coll_config.postUploadFunction
                        existing.max_file_size_mb = coll_config.maxFileSizeMb
                        existing.max_total_size_gb = coll_config.maxTotalSizeGb
                        existing.is_public = coll_config.isPublic
                        existing.allow_shared_files = coll_config.allowSharedFiles
                        existing.allow_private_files = coll_config.allowPrivateFiles
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "collections", resource_name)
                    self.collection_ids[resource_name] = str(existing.id)

                else:
                    if not dry_run:
                        new_collection = Collection(
                            namespace=coll_config.namespace,
                            name=coll_config.name,
                            user_id=self.owner_user_id,
                            metadata_schema=coll_config.metadataSchema or {},
                            content_filter_function=coll_config.contentFilterFunction,
                            post_upload_function=coll_config.postUploadFunction,
                            max_file_size_mb=coll_config.maxFileSizeMb,
                            max_total_size_gb=coll_config.maxTotalSizeGb,
                            is_public=coll_config.isPublic,
                            allow_shared_files=coll_config.allowSharedFiles,
                            allow_private_files=coll_config.allowPrivateFiles,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_collection)
                        await self.db.flush()
                        self.collection_ids[resource_name] = str(new_collection.id)
                    else:
                        self.collection_ids[resource_name] = "dry-run-id"

                    self._track_change("create", "collections", resource_name)

            except Exception as e:
                self.errors.append(f"Error applying collection '{resource_name}': {str(e)}")

    def _normalize_store_references(self, store_refs: list) -> list[dict]:
        """Normalize store references to dict format."""
        normalized = []
        for ref in store_refs:
            if isinstance(ref, str):
                normalized.append({"store": ref, "access": "readwrite"})
            elif hasattr(ref, 'model_dump'):
                normalized.append(ref.model_dump())
            elif isinstance(ref, dict):
                normalized.append(ref)
        return normalized

    async def _apply_stores(self, stores, dry_run: bool):
        """Apply store configurations"""
        for store_config in stores:
            resource_name = f"{store_config.namespace}/{store_config.name}"
            try:
                stmt = select(Store).where(
                    Store.namespace == store_config.namespace,
                    Store.name == store_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": store_config.namespace,
                        "name": store_config.name,
                        "description": store_config.description,
                        "schema": store_config.schema or {},
                        "strict": store_config.strict,
                        "default_visibility": store_config.defaultVisibility,
                        "encrypted": store_config.encrypted,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Store '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "stores", resource_name)
                        self.store_ids[resource_name] = str(existing.id)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "stores", resource_name)
                        self.store_ids[resource_name] = str(existing.id)
                        continue

                    if not dry_run:
                        existing.description = store_config.description
                        existing.schema = store_config.schema or {}
                        existing.strict = store_config.strict
                        existing.default_visibility = store_config.defaultVisibility
                        existing.encrypted = store_config.encrypted
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "stores", resource_name)
                    self.store_ids[resource_name] = str(existing.id)

                else:
                    if not dry_run:
                        new_store = Store(
                            namespace=store_config.namespace,
                            name=store_config.name,
                            description=store_config.description,
                            schema=store_config.schema or {},
                            strict=store_config.strict,
                            default_visibility=store_config.defaultVisibility,
                            encrypted=store_config.encrypted,
                            user_id=self.owner_user_id,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_store)
                        await self.db.flush()
                        self.store_ids[resource_name] = str(new_store.id)
                    else:
                        self.store_ids[resource_name] = "dry-run-id"

                    self._track_change("create", "stores", resource_name)

            except Exception as e:
                self.errors.append(f"Error applying store '{resource_name}': {str(e)}")

    async def _apply_apps(self, apps, dry_run: bool):
        """Apply app registration configurations"""
        for app_config in apps:
            resource_name = f"{app_config.namespace}/{app_config.name}"
            try:
                stmt = select(App).where(
                    App.namespace == app_config.namespace,
                    App.name == app_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": app_config.namespace,
                        "name": app_config.name,
                        "description": app_config.description,
                        "required_resources": [
                            {"type": r.type, "namespace": r.namespace, "name": r.name}
                            for r in app_config.requiredResources
                        ],
                        "required_permissions": sorted(app_config.requiredPermissions),
                        "optional_permissions": sorted(app_config.optionalPermissions),
                        "exposed_namespaces": {
                            k: sorted(v) for k, v in sorted(app_config.exposedNamespaces.items())
                        },
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"App '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "apps", resource_name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "apps", resource_name)
                        continue

                    if not dry_run:
                        existing.description = app_config.description
                        existing.required_resources = [
                            {"type": r.type, "namespace": r.namespace, "name": r.name}
                            for r in app_config.requiredResources
                        ]
                        existing.required_permissions = app_config.requiredPermissions
                        existing.optional_permissions = app_config.optionalPermissions
                        existing.exposed_namespaces = app_config.exposedNamespaces
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "apps", resource_name)

                else:
                    if not dry_run:
                        new_app = App(
                            namespace=app_config.namespace,
                            name=app_config.name,
                            description=app_config.description,
                            required_resources=[
                                {"type": r.type, "namespace": r.namespace, "name": r.name}
                                for r in app_config.requiredResources
                            ],
                            required_permissions=app_config.requiredPermissions,
                            optional_permissions=app_config.optionalPermissions,
                            exposed_namespaces=app_config.exposedNamespaces,
                            user_id=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_app)

                    self._track_change("create", "apps", resource_name)

            except Exception as e:
                self.errors.append(f"Error applying app '{resource_name}': {str(e)}")

    async def _apply_agents(self, agents, dry_run: bool):
        """Apply agent configurations"""
        for agent_config in agents:
            try:
                stmt = select(Agent).where(
                    Agent.namespace == agent_config.namespace, Agent.name == agent_config.name
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Normalize function references to namespace/name format
                normalized_functions = (
                    self._normalize_function_references(agent_config.enabledFunctions)
                    if agent_config.enabledFunctions
                    else []
                )

                # Normalize skill references to dict format
                normalized_skills = (
                    self._normalize_skill_references(agent_config.enabledSkills)
                    if agent_config.enabledSkills
                    else []
                )

                # Normalize store references to dict format
                normalized_stores = (
                    self._normalize_store_references(agent_config.enabledStores)
                    if agent_config.enabledStores
                    else []
                )

                config_hash = self._calculate_hash(
                    {
                        "namespace": agent_config.namespace,
                        "name": agent_config.name,
                        "description": agent_config.description,
                        "llm_provider": agent_config.llmProviderName,
                        "model": agent_config.model,
                        "temperature": agent_config.temperature,
                        "max_tokens": agent_config.maxTokens,
                        "system_prompt": agent_config.systemPrompt,
                        "enabled_functions": sorted(normalized_functions),
                        "function_parameters": agent_config.functionParameters
                        if agent_config.functionParameters
                        else {},
                        "status_templates": agent_config.statusTemplates
                        if agent_config.statusTemplates
                        else {},
                        "enabled_agents": sorted(agent_config.enabledAgents)
                        if agent_config.enabledAgents
                        else [],
                        "enabled_skills": sorted(normalized_skills, key=lambda x: x["skill"])
                        if normalized_skills
                        else [],
                        "enabled_stores": sorted(normalized_stores, key=lambda x: x["store"])
                        if normalized_stores
                        else [],
                        "enabled_queries": sorted(agent_config.enabledQueries)
                        if agent_config.enabledQueries
                        else [],
                        "query_parameters": agent_config.queryParameters
                        if agent_config.queryParameters
                        else {},
                        "enabled_collections": sorted(agent_config.enabledCollections)
                        if agent_config.enabledCollections
                        else [],
                        "enabled_components": sorted(agent_config.enabledComponents)
                        if agent_config.enabledComponents
                        else [],
                        "icon": agent_config.icon,
                        "is_default": agent_config.isDefault,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Agent '{agent_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
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
                            llm_provider_id = self.llm_provider_ids.get(
                                agent_config.llmProviderName
                            )
                        existing.llm_provider_id = llm_provider_id

                        existing.description = agent_config.description
                        existing.model = agent_config.model
                        existing.temperature = agent_config.temperature
                        existing.max_tokens = agent_config.maxTokens
                        existing.system_prompt = agent_config.systemPrompt
                        existing.enabled_functions = normalized_functions
                        existing.function_parameters = agent_config.functionParameters
                        existing.status_templates = agent_config.statusTemplates
                        existing.enabled_agents = agent_config.enabledAgents
                        existing.enabled_skills = normalized_skills
                        existing.enabled_stores = normalized_stores
                        existing.enabled_queries = agent_config.enabledQueries
                        existing.query_parameters = agent_config.queryParameters
                        existing.enabled_collections = agent_config.enabledCollections
                        existing.enabled_components = agent_config.enabledComponents
                        existing.icon = agent_config.icon
                        if agent_config.isDefault:
                            await self.db.execute(
                                Agent.__table__.update()
                                .where(Agent.id != existing.id)
                                .values(is_default=False)
                            )
                        existing.is_default = agent_config.isDefault
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "agents", agent_config.name)
                    self.agent_ids[agent_config.name] = str(existing.id)

                else:
                    if not dry_run:
                        # Get LLM provider ID (None if not specified = use default)
                        llm_provider_id = None
                        if agent_config.llmProviderName:
                            llm_provider_id = self.llm_provider_ids.get(
                                agent_config.llmProviderName
                            )

                        if agent_config.isDefault:
                            await self.db.execute(
                                Agent.__table__.update().values(is_default=False)
                            )

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
                            status_templates=agent_config.statusTemplates,
                            enabled_agents=agent_config.enabledAgents,
                            enabled_skills=normalized_skills,
                            enabled_stores=normalized_stores,
                            enabled_queries=agent_config.enabledQueries,
                            query_parameters=agent_config.queryParameters,
                            enabled_collections=agent_config.enabledCollections,
                            enabled_components=agent_config.enabledComponents,
                            icon=agent_config.icon,
                            is_default=agent_config.isDefault,
                            user_id=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
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

                config_hash = self._calculate_hash(
                    {
                        "path": webhook_config.path,
                        "function_name": webhook_config.functionName,
                        "http_method": webhook_config.httpMethod,
                        "description": webhook_config.description,
                        "requires_auth": webhook_config.requiresAuth,
                        "default_values": webhook_config.defaultValues,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Webhook '{webhook_config.path}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "webhooks", webhook_config.path)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "webhooks", webhook_config.path)
                        continue

                    if not dry_run:
                        # Parse function reference (may be "namespace/name" or just "name")
                        func_ref = webhook_config.functionName
                        if "/" in func_ref:
                            func_ns, func_name = func_ref.split("/", 1)
                        else:
                            func_ns, func_name = "default", func_ref

                        existing.function_namespace = func_ns
                        existing.function_name = func_name
                        existing.http_method = webhook_config.httpMethod
                        existing.description = webhook_config.description
                        existing.requires_auth = webhook_config.requiresAuth
                        existing.default_values = webhook_config.defaultValues
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "webhooks", webhook_config.path)

                else:
                    if not dry_run:
                        # Parse function reference (may be "namespace/name" or just "name")
                        func_ref = webhook_config.functionName
                        if "/" in func_ref:
                            func_ns, func_name = func_ref.split("/", 1)
                        else:
                            func_ns, func_name = "default", func_ref

                        new_webhook = Webhook(
                            path=webhook_config.path,
                            function_namespace=func_ns,
                            function_name=func_name,
                            user_id=self.owner_user_id,
                            http_method=webhook_config.httpMethod,
                            description=webhook_config.description,
                            requires_auth=webhook_config.requiresAuth,
                            default_values=webhook_config.defaultValues,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_webhook)

                    self._track_change("create", "webhooks", webhook_config.path)

            except Exception as e:
                self.errors.append(f"Error applying webhook '{webhook_config.path}': {str(e)}")

    async def _apply_templates(self, templates, dry_run: bool):
        """Apply template configurations"""
        for tmpl_config in templates:
            resource_name = f"{tmpl_config.namespace}/{tmpl_config.name}"
            try:
                stmt = select(Template).where(
                    Template.namespace == tmpl_config.namespace,
                    Template.name == tmpl_config.name,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "namespace": tmpl_config.namespace,
                        "name": tmpl_config.name,
                        "description": tmpl_config.description,
                        "title": tmpl_config.title,
                        "html_content": tmpl_config.htmlContent,
                        "text_content": tmpl_config.textContent,
                        "variable_schema": tmpl_config.variableSchema or {},
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Template '{resource_name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "templates", resource_name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "templates", resource_name)
                        continue

                    if not dry_run:
                        existing.description = tmpl_config.description
                        existing.title = tmpl_config.title
                        existing.html_content = tmpl_config.htmlContent
                        existing.text_content = tmpl_config.textContent
                        existing.variable_schema = tmpl_config.variableSchema or {}
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "templates", resource_name)

                else:
                    if not dry_run:
                        new_template = Template(
                            namespace=tmpl_config.namespace,
                            name=tmpl_config.name,
                            description=tmpl_config.description,
                            title=tmpl_config.title,
                            html_content=tmpl_config.htmlContent,
                            text_content=tmpl_config.textContent,
                            variable_schema=tmpl_config.variableSchema or {},
                            user_id=self.owner_user_id,
                            created_by=self.owner_user_id,
                            updated_by=self.owner_user_id,
                            is_active=True,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_template)

                    self._track_change("create", "templates", resource_name)

            except Exception as e:
                self.errors.append(f"Error applying template '{resource_name}': {str(e)}")

    async def _apply_schedules(self, schedules, dry_run: bool):
        """Apply schedule configurations"""
        for schedule_config in schedules:
            try:
                stmt = select(ScheduledJob).where(ScheduledJob.name == schedule_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Determine target namespace and name
                schedule_type = schedule_config.scheduleType
                if schedule_type == "agent":
                    agent_ref = schedule_config.agentName or ""
                    if "/" in agent_ref:
                        target_namespace, target_name = agent_ref.split("/", 1)
                    else:
                        target_namespace, target_name = "default", agent_ref
                else:
                    func_ref = schedule_config.functionName or ""
                    if "/" in func_ref:
                        target_namespace, target_name = func_ref.split("/", 1)
                    else:
                        target_namespace, target_name = "default", func_ref

                config_hash = self._calculate_hash(
                    {
                        "name": schedule_config.name,
                        "schedule_type": schedule_type,
                        "target_namespace": target_namespace,
                        "target_name": target_name,
                        "content": schedule_config.content,
                        "cron_expression": schedule_config.cronExpression,
                        "timezone": schedule_config.timezone,
                        "input_data": schedule_config.inputData,
                        "is_active": schedule_config.isActive,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Schedule '{schedule_config.name}' exists but is not managed by '{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "schedules", schedule_config.name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "schedules", schedule_config.name)
                        continue

                    if not dry_run:
                        existing.schedule_type = schedule_type
                        existing.target_namespace = target_namespace
                        existing.target_name = target_name
                        existing.content = schedule_config.content
                        existing.cron_expression = schedule_config.cronExpression
                        existing.timezone = schedule_config.timezone
                        existing.input_data = schedule_config.inputData
                        existing.is_active = schedule_config.isActive
                        existing.config_checksum = config_hash

                    self._track_change("update", "schedules", schedule_config.name)

                else:
                    if not dry_run:
                        new_schedule = ScheduledJob(
                            name=schedule_config.name,
                            schedule_type=schedule_type,
                            target_namespace=target_namespace,
                            target_name=target_name,
                            content=schedule_config.content,
                            cron_expression=schedule_config.cronExpression,
                            timezone=schedule_config.timezone,
                            input_data=schedule_config.inputData,
                            is_active=schedule_config.isActive,
                            user_id=self.owner_user_id,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_schedule)

                    self._track_change("create", "schedules", schedule_config.name)

            except Exception as e:
                self.errors.append(f"Error applying schedule '{schedule_config.name}': {str(e)}")

    async def _apply_database_triggers(self, triggers, dry_run: bool):
        """Apply database trigger (CDC) configurations"""
        for trigger_config in triggers:
            try:
                # Resolve connection name -> id
                conn_result = await self.db.execute(
                    select(DatabaseConnection).where(
                        DatabaseConnection.name == trigger_config.connectionName
                    )
                )
                db_conn = conn_result.scalar_one_or_none()
                if not db_conn:
                    self.errors.append(
                        f"Database trigger '{trigger_config.name}': "
                        f"connection '{trigger_config.connectionName}' not found"
                    )
                    continue

                # Parse function name (namespace/name format)
                func_ref = trigger_config.functionName
                if "/" in func_ref:
                    func_namespace, func_name = func_ref.split("/", 1)
                else:
                    func_namespace, func_name = "default", func_ref

                # Look for existing trigger
                stmt = select(DatabaseTrigger).where(DatabaseTrigger.name == trigger_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                config_hash = self._calculate_hash(
                    {
                        "name": trigger_config.name,
                        "connection_id": str(db_conn.id),
                        "schema_name": trigger_config.schemaName,
                        "table_name": trigger_config.tableName,
                        "operations": trigger_config.operations,
                        "function_namespace": func_namespace,
                        "function_name": func_name,
                        "poll_column": trigger_config.pollColumn,
                        "poll_interval_seconds": trigger_config.pollIntervalSeconds,
                        "batch_size": trigger_config.batchSize,
                        "is_active": trigger_config.isActive,
                    }
                )

                if existing:
                    if existing.managed_by != self.managed_by:
                        self.warnings.append(
                            f"Database trigger '{trigger_config.name}' exists but is not managed by "
                            f"'{self.managed_by}'. Skipping."
                        )
                        self._track_change("unchanged", "databaseTriggers", trigger_config.name)
                        continue

                    if existing.config_checksum == config_hash:
                        self._track_change("unchanged", "databaseTriggers", trigger_config.name)
                        continue

                    if not dry_run:
                        existing.database_connection_id = db_conn.id
                        existing.schema_name = trigger_config.schemaName
                        existing.table_name = trigger_config.tableName
                        existing.operations = trigger_config.operations
                        existing.function_namespace = func_namespace
                        existing.function_name = func_name
                        existing.poll_column = trigger_config.pollColumn
                        existing.poll_interval_seconds = trigger_config.pollIntervalSeconds
                        existing.batch_size = trigger_config.batchSize
                        existing.is_active = trigger_config.isActive
                        existing.config_checksum = config_hash

                    self._track_change("update", "databaseTriggers", trigger_config.name)

                else:
                    if not dry_run:
                        new_trigger = DatabaseTrigger(
                            name=trigger_config.name,
                            database_connection_id=db_conn.id,
                            schema_name=trigger_config.schemaName,
                            table_name=trigger_config.tableName,
                            operations=trigger_config.operations,
                            function_namespace=func_namespace,
                            function_name=func_name,
                            poll_column=trigger_config.pollColumn,
                            poll_interval_seconds=trigger_config.pollIntervalSeconds,
                            batch_size=trigger_config.batchSize,
                            is_active=trigger_config.isActive,
                            user_id=self.owner_user_id,
                            managed_by=self.managed_by,
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_trigger)

                    self._track_change("create", "databaseTriggers", trigger_config.name)

            except Exception as e:
                self.errors.append(
                    f"Error applying database trigger '{trigger_config.name}': {str(e)}"
                )
