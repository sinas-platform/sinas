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
from app.models.schedule import ScheduledJob
from app.models.skill import Skill
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

    def __init__(self, db: AsyncSession, config_name: str):
        self.db = db
        self.config_name = config_name
        self.summary = ConfigApplySummary()
        self.changes: list[ResourceChange] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

        # Resource lookup caches (name -> id)
        self.group_ids: dict[str, str] = {}
        self.user_ids: dict[str, str] = {}
        self.datasource_ids: dict[str, str] = {}
        self.function_ids: dict[str, str] = {}
        self.agent_ids: dict[str, str] = {}
        self.llm_provider_ids: dict[str, str] = {}
        self.database_connection_ids: dict[str, str] = {}
        self.webhook_ids: dict[str, str] = {}
        self.collection_ids: dict[str, str] = {}
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
            await self._apply_groups(config.spec.groups, dry_run)
            await self._apply_users(config.spec.users, dry_run)
            await self._apply_llm_providers(config.spec.llmProviders, dry_run)
            await self._apply_database_connections(config.spec.databaseConnections, dry_run)

            await self._apply_functions(config.spec.functions, dry_run)
            await self._apply_skills(config.spec.skills, dry_run)
            await self._apply_queries(config.spec.queries, dry_run)
            await self._apply_collections(config.spec.collections, dry_run)
            await self._apply_apps(config.spec.apps, dry_run)
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

    async def _apply_groups(self, groups, dry_run: bool):
        """Apply group configurations"""
        for group_config in groups:
            try:
                # Check if exists
                stmt = select(Role).where(Role.name == group_config.name)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                # Calculate hash
                config_hash = self._calculate_hash(
                    {
                        "name": group_config.name,
                        "description": group_config.description,
                        "email_domain": group_config.emailDomain,
                    }
                )

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

                    self._track_change(
                        "update", "groups", group_config.name, details="Updated group configuration"
                    )
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

                    self._track_change(
                        "create", "groups", group_config.name, details="Created new group"
                    )

                # Apply permissions
                if not dry_run and group_config.permissions:
                    await self._apply_group_permissions(
                        self.group_ids[group_config.name], group_config.permissions
                    )

            except Exception as e:
                self.errors.append(f"Error applying group '{group_config.name}': {str(e)}")

    async def _apply_group_permissions(self, group_id: str, permissions):
        """Apply permissions to a group"""
        # Delete existing config-managed permissions
        from sqlalchemy import delete

        stmt = delete(RolePermission).where(
            and_(RolePermission.group_id == group_id, RolePermission.managed_by == "config")
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

                config_hash = self._calculate_hash(
                    {
                        "email": user_config.email,
                        "is_active": user_config.isActive,
                        "groups": sorted(user_config.groups),
                    }
                )

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
                        self.user_ids[user_config.email], user_config.groups
                    )

            except Exception as e:
                self.errors.append(f"Error applying user '{user_config.email}': {str(e)}")

    async def _apply_user_groups(self, user_id: str, group_names: list[str]):
        """Apply group memberships to a user"""
        # Remove existing config-managed memberships
        from sqlalchemy import delete

        stmt = delete(UserRole).where(
            and_(UserRole.user_id == user_id, UserRole.managed_by == "config")
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
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Database connection '{conn_config.name}' exists but is not config-managed. Skipping."
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
                            managed_by="config",
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
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Query '{resource_name}' exists but is not config-managed. Skipping."
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
                        # Get user from group
                        from app.models.user import Role, UserRole

                        group_id = self.group_ids.get(query_config.groupName)
                        if not group_id:
                            stmt = select(Role).where(Role.name == query_config.groupName)
                            result = await self.db.execute(stmt)
                            group = result.scalar_one_or_none()
                            if group:
                                group_id = str(group.id)

                        if not group_id:
                            self.errors.append(
                                f"Group '{query_config.groupName}' not found for query '{resource_name}'"
                            )
                            continue

                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{query_config.groupName}' for query '{resource_name}'"
                            )
                            continue

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
                            user_id=member.user_id,
                            is_active=True,
                            managed_by="config",
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
                    }
                )

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
                            self.errors.append(
                                f"Group '{func_config.groupName}' not found for function '{func_config.name}'"
                            )
                            continue

                        # Get a user from the group for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{func_config.groupName}' for function '{func_config.name}'"
                            )
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
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Skill '{skill_config.namespace}/{skill_config.name}' exists but is not config-managed. Skipping."
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
                        # Get admin user for created_by (skills are typically system-wide)
                        # Use first admin user
                        from app.models.user import Role, UserRole

                        stmt = select(Role).where(Role.name == "Admins")
                        result = await self.db.execute(stmt)
                        admin_role = result.scalar_one_or_none()

                        if not admin_role:
                            self.errors.append(
                                f"Admins role not found for skill '{skill_config.namespace}/{skill_config.name}'"
                            )
                            continue

                        stmt = select(UserRole).where(UserRole.role_id == admin_role.id).limit(1)
                        result = await self.db.execute(stmt)
                        admin_member = result.scalar_one_or_none()

                        if not admin_member:
                            self.errors.append(
                                f"No admin users found for skill '{skill_config.namespace}/{skill_config.name}'"
                            )
                            continue

                        new_skill = Skill(
                            namespace=skill_config.namespace,
                            name=skill_config.name,
                            description=skill_config.description,
                            content=skill_config.content,
                            user_id=admin_member.user_id,
                            is_active=True,
                            managed_by="config",
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
                        "allow_shared_files": coll_config.allowSharedFiles,
                        "allow_private_files": coll_config.allowPrivateFiles,
                    }
                )

                if existing:
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"Collection '{resource_name}' exists but is not config-managed. Skipping."
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
                        existing.allow_shared_files = coll_config.allowSharedFiles
                        existing.allow_private_files = coll_config.allowPrivateFiles
                        existing.config_checksum = config_hash
                        existing.updated_at = datetime.utcnow()

                    self._track_change("update", "collections", resource_name)
                    self.collection_ids[resource_name] = str(existing.id)

                else:
                    if not dry_run:
                        group_id = self.group_ids.get(coll_config.groupName)
                        if not group_id:
                            self.errors.append(
                                f"Group '{coll_config.groupName}' not found for collection '{resource_name}'"
                            )
                            continue

                        # Get user from the group for ownership
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{coll_config.groupName}' for collection '{resource_name}'"
                            )
                            continue

                        new_collection = Collection(
                            namespace=coll_config.namespace,
                            name=coll_config.name,
                            user_id=member.user_id,
                            metadata_schema=coll_config.metadataSchema or {},
                            content_filter_function=coll_config.contentFilterFunction,
                            post_upload_function=coll_config.postUploadFunction,
                            max_file_size_mb=coll_config.maxFileSizeMb,
                            max_total_size_gb=coll_config.maxTotalSizeGb,
                            allow_shared_files=coll_config.allowSharedFiles,
                            allow_private_files=coll_config.allowPrivateFiles,
                            managed_by="config",
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
                    if existing.managed_by != "config":
                        self.warnings.append(
                            f"App '{resource_name}' exists but is not config-managed. Skipping."
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
                        # Get admin user for ownership
                        from app.models.user import Role, UserRole

                        stmt = select(Role).where(Role.name == "Admins")
                        result = await self.db.execute(stmt)
                        admin_role = result.scalar_one_or_none()

                        if not admin_role:
                            self.errors.append(
                                f"Admins role not found for app '{resource_name}'"
                            )
                            continue

                        stmt = select(UserRole).where(UserRole.role_id == admin_role.id).limit(1)
                        result = await self.db.execute(stmt)
                        admin_member = result.scalar_one_or_none()

                        if not admin_member:
                            self.errors.append(
                                f"No admin users found for app '{resource_name}'"
                            )
                            continue

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
                            user_id=admin_member.user_id,
                            is_active=True,
                            managed_by="config",
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
                        "enabled_agents": sorted(agent_config.enabledAgents)
                        if agent_config.enabledAgents
                        else [],
                        "enabled_skills": sorted(normalized_skills, key=lambda x: x["skill"])
                        if normalized_skills
                        else [],
                        "state_namespaces_readonly": sorted(agent_config.stateNamespacesReadonly)
                        if agent_config.stateNamespacesReadonly
                        else [],
                        "state_namespaces_readwrite": sorted(agent_config.stateNamespacesReadwrite)
                        if agent_config.stateNamespacesReadwrite
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
                        "is_default": agent_config.isDefault,
                    }
                )

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
                        existing.enabled_agents = agent_config.enabledAgents
                        existing.enabled_skills = normalized_skills
                        existing.state_namespaces_readonly = agent_config.stateNamespacesReadonly
                        existing.state_namespaces_readwrite = agent_config.stateNamespacesReadwrite
                        existing.enabled_queries = agent_config.enabledQueries
                        existing.query_parameters = agent_config.queryParameters
                        existing.enabled_collections = agent_config.enabledCollections
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
                        group_id = self.group_ids.get(agent_config.groupName)
                        if not group_id:
                            self.errors.append(
                                f"Group '{agent_config.groupName}' not found for agent '{agent_config.name}'"
                            )
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{agent_config.groupName}' for agent '{agent_config.name}'"
                            )
                            continue

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
                            enabled_agents=agent_config.enabledAgents,
                            enabled_skills=normalized_skills,
                            state_namespaces_readonly=agent_config.stateNamespacesReadonly,
                            state_namespaces_readwrite=agent_config.stateNamespacesReadwrite,
                            enabled_queries=agent_config.enabledQueries,
                            query_parameters=agent_config.queryParameters,
                            enabled_collections=agent_config.enabledCollections,
                            is_default=agent_config.isDefault,
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
                            self.errors.append(
                                f"Function '{webhook_config.functionName}' not found for webhook '{webhook_config.path}'"
                            )
                            continue

                        group_id = self.group_ids.get(webhook_config.groupName)
                        if not group_id:
                            self.errors.append(
                                f"Group '{webhook_config.groupName}' not found for webhook '{webhook_config.path}'"
                            )
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{webhook_config.groupName}' for webhook '{webhook_config.path}'"
                            )
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
                        group_id = self.group_ids.get(schedule_config.groupName)
                        if not group_id:
                            self.errors.append(
                                f"Group '{schedule_config.groupName}' not found for schedule '{schedule_config.name}'"
                            )
                            continue

                        # Get user for created_by
                        stmt = select(UserRole).where(UserRole.role_id == group_id).limit(1)
                        result = await self.db.execute(stmt)
                        member = result.scalar_one_or_none()
                        if not member:
                            self.errors.append(
                                f"No users in group '{schedule_config.groupName}' for schedule '{schedule_config.name}'"
                            )
                            continue

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
                            user_id=member.user_id,
                            managed_by="config",
                            config_name=self.config_name,
                            config_checksum=config_hash,
                        )
                        self.db.add(new_schedule)

                    self._track_change("create", "schedules", schedule_config.name)

            except Exception as e:
                self.errors.append(f"Error applying schedule '{schedule_config.name}': {str(e)}")
