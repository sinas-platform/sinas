"""
Configuration apply service
Handles idempotent application of declarative configuration
"""
import hashlib
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.config import (
    ConfigApplyResponse,
    ConfigApplySummary,
    ResourceChange,
    SinasConfig,
)

from app.services.config_apply.identity import apply_roles, apply_users
from app.services.config_apply.data_sources import (
    apply_database_connections,
    apply_llm_providers,
)
from app.services.config_apply.resources import (
    apply_collections,
    apply_components,
    apply_connectors,
    apply_functions,
    apply_manifests,
    apply_queries,
    apply_secrets,
    apply_skills,
    apply_stores,
)
from app.services.config_apply.agents import apply_agents
from app.services.config_apply.integrations import (
    apply_database_triggers,
    apply_schedules,
    apply_templates,
    apply_webhooks,
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
            # Common kwargs shared by all appliers
            common = dict(
                db=self.db,
                dry_run=dry_run,
                managed_by=self.managed_by,
                config_name=self.config_name,
                calculate_hash=self._calculate_hash,
                track_change=self._track_change,
                errors=self.errors,
                warnings=self.warnings,
            )
            common_with_owner = dict(**common, owner_user_id=self.owner_user_id)

            # Apply resources in dependency order
            if "roles" not in self.skip_resource_types:
                await apply_roles(
                    **common,
                    roles=config.spec.roles,
                    role_ids=self.role_ids,
                )
            if "users" not in self.skip_resource_types:
                await apply_users(
                    **common,
                    users=config.spec.users,
                    role_ids=self.role_ids,
                    user_ids=self.user_ids,
                )
            if "llmProviders" not in self.skip_resource_types:
                await apply_llm_providers(
                    **common,
                    providers=config.spec.llmProviders,
                    llm_provider_ids=self.llm_provider_ids,
                )
            if "databaseConnections" not in self.skip_resource_types:
                await apply_database_connections(
                    **common,
                    connections=config.spec.databaseConnections,
                    database_connection_ids=self.database_connection_ids,
                )

            if "secrets" not in self.skip_resource_types:
                await apply_secrets(
                    **common_with_owner,
                    secrets=config.spec.secrets,
                )

            if "connectors" not in self.skip_resource_types:
                await apply_connectors(
                    **common_with_owner,
                    connectors=config.spec.connectors,
                )

            if "functions" not in self.skip_resource_types:
                await apply_functions(
                    **common_with_owner,
                    functions=config.spec.functions,
                    function_ids=self.function_ids,
                )
            if "skills" not in self.skip_resource_types:
                await apply_skills(
                    **common_with_owner,
                    skills=config.spec.skills,
                )
            if "components" not in self.skip_resource_types:
                await apply_components(
                    **common_with_owner,
                    components=config.spec.components,
                )
            if "queries" not in self.skip_resource_types:
                await apply_queries(
                    **common_with_owner,
                    queries=config.spec.queries,
                    database_connection_ids=self.database_connection_ids,
                )
            if "collections" not in self.skip_resource_types:
                await apply_collections(
                    **common_with_owner,
                    collections=config.spec.collections,
                    collection_ids=self.collection_ids,
                )
            if "templates" not in self.skip_resource_types:
                await apply_templates(
                    **common_with_owner,
                    templates=config.spec.templates,
                )
            if "stores" not in self.skip_resource_types:
                await apply_stores(
                    **common_with_owner,
                    stores=config.spec.stores,
                    store_ids=self.store_ids,
                )
            if "manifests" not in self.skip_resource_types:
                await apply_manifests(
                    **common_with_owner,
                    manifests=config.spec.manifests,
                )
            if "agents" not in self.skip_resource_types:
                await apply_agents(
                    **common_with_owner,
                    agents=config.spec.agents,
                    llm_provider_ids=self.llm_provider_ids,
                    agent_ids=self.agent_ids,
                )
            if "webhooks" not in self.skip_resource_types:
                await apply_webhooks(
                    **common_with_owner,
                    webhooks=config.spec.webhooks,
                )
            if "schedules" not in self.skip_resource_types:
                await apply_schedules(
                    **common_with_owner,
                    schedules=config.spec.schedules,
                )
            if "databaseTriggers" not in self.skip_resource_types:
                await apply_database_triggers(
                    **common_with_owner,
                    triggers=config.spec.databaseTriggers,
                )

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
