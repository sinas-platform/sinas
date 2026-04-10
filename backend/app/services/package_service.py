"""
Package service for installing, uninstalling, and managing integration packages.
"""
import logging
from typing import Optional

import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connector import Connector
from app.models.manifest import Manifest
from app.models.component import Component
from app.models.database_trigger import DatabaseTrigger
from app.models.file import Collection
from app.models.function import Function
from app.models.package import Package
from app.models.query import Query
from app.models.schedule import ScheduledJob
from app.models.skill import Skill
from app.models.store import Store
from app.models.template import Template
from app.models.webhook import Webhook
from app.schemas.config import ConfigApplyResponse, SinasConfig
from app.services.config_apply import ConfigApplyService
from app.services.config_export import ConfigExportService, _remove_none_values
from app.services.config_parser import ConfigParser

logger = logging.getLogger(__name__)

# Resource types that packages cannot include (environment-specific)
PACKAGE_SKIP_TYPES = {"roles", "users", "llmProviders", "databaseConnections"}

# Models that support managed_by
MANAGED_MODELS = [Agent, Connector, Manifest, Component, Collection, DatabaseTrigger, Function, Query, ScheduledJob, Skill, Store, Template, Webhook]


def detach_if_package_managed(resource) -> bool:
    """
    If a resource is managed by a package, clear its managed_by to detach it.
    Call this in update endpoints before modifying fields.

    Returns True if the resource was detached, False otherwise.
    """
    if hasattr(resource, "managed_by") and resource.managed_by and resource.managed_by.startswith("pkg:"):
        resource.managed_by = None
        resource.config_name = None
        resource.config_checksum = None
        return True
    return False


class PackageService:
    """Service for managing installable integration packages."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def install(self, yaml_content: str, user_id: str) -> tuple[Package, ConfigApplyResponse]:
        """
        Install a package from YAML content.

        Args:
            yaml_content: YAML string with kind: SinasPackage
            user_id: ID of the user installing the package

        Returns:
            Tuple of (Package record, ConfigApplyResponse)
        """
        config, validation = await ConfigParser.parse_and_validate(yaml_content, db=self.db)

        if not validation.is_valid:
            errors = [str(e) for e in validation.errors]
            raise ValueError(f"Package validation failed: {'; '.join(errors)}")

        if config.kind != "SinasPackage":
            raise ValueError("Expected kind: SinasPackage")

        if not config.package:
            raise ValueError("Package metadata is required for SinasPackage")

        pkg_name = config.package.name
        managed_by = f"pkg:{pkg_name}"

        # Check if already installed — upgrade in place if so
        existing_result = await self.db.execute(
            select(Package).where(Package.name == pkg_name)
        )
        existing_package = existing_result.scalar_one_or_none()

        # Apply config with package managed_by, skip environment-specific types, no auto-commit
        apply_service = ConfigApplyService(
            db=self.db,
            config_name=pkg_name,
            owner_user_id=user_id,
            managed_by=managed_by,
            auto_commit=False,
            skip_resource_types=PACKAGE_SKIP_TYPES,
        )

        result = await apply_service.apply_config(config, dry_run=False)

        if not result.success:
            raise ValueError(f"Package apply failed: {'; '.join(result.errors)}")

        # Add validation warnings to result
        result.warnings.extend(validation.warnings)

        # Create or update package record
        if existing_package:
            existing_package.version = config.package.version
            existing_package.description = config.package.description
            existing_package.author = config.package.author
            existing_package.source_url = config.package.url
            existing_package.source_yaml = yaml_content
            package = existing_package
        else:
            package = Package(
                name=pkg_name,
                version=config.package.version,
                description=config.package.description,
                author=config.package.author,
                source_url=config.package.url,
                source_yaml=yaml_content,
                installed_by=user_id,
            )
            self.db.add(package)

        # Single commit for everything
        await self.db.commit()
        await self.db.refresh(package)

        return package, result

    async def preview(self, yaml_content: str, user_id: str) -> ConfigApplyResponse:
        """
        Preview a package install (dry run).

        Returns what would be created/updated without making changes.
        """
        config, validation = await ConfigParser.parse_and_validate(yaml_content, db=self.db)

        if not validation.is_valid:
            errors = [str(e) for e in validation.errors]
            raise ValueError(f"Package validation failed: {'; '.join(errors)}")

        if config.kind != "SinasPackage":
            raise ValueError("Expected kind: SinasPackage")

        if not config.package:
            raise ValueError("Package metadata is required for SinasPackage")

        pkg_name = config.package.name
        managed_by = f"pkg:{pkg_name}"

        apply_service = ConfigApplyService(
            db=self.db,
            config_name=pkg_name,
            owner_user_id=user_id,
            managed_by=managed_by,
            auto_commit=False,
            skip_resource_types=PACKAGE_SKIP_TYPES,
        )

        result = await apply_service.apply_config(config, dry_run=True)
        result.warnings.extend(validation.warnings)
        return result

    async def uninstall(self, package_name: str) -> dict:
        """
        Uninstall a package: delete all resources with matching managed_by and the package record.

        Returns dict with deleted counts per resource type.
        """
        managed_by = f"pkg:{package_name}"

        # Check package exists
        result = await self.db.execute(
            select(Package).where(Package.name == package_name)
        )
        package = result.scalar_one_or_none()
        if not package:
            raise ValueError(f"Package '{package_name}' is not installed")

        deleted_counts = {}

        # Delete managed resources across all model types
        model_names = {
            Agent: "agents",
            Connector: "connectors",
            Manifest: "manifests",
            Component: "components",
            Collection: "collections",
            DatabaseTrigger: "databaseTriggers",
            Function: "functions",
            Query: "queries",
            ScheduledJob: "schedules",
            Skill: "skills",
            Store: "stores",
            Template: "templates",
            Webhook: "webhooks",
        }

        for model, type_name in model_names.items():
            stmt = delete(model).where(model.managed_by == managed_by)
            result = await self.db.execute(stmt)
            if result.rowcount > 0:
                deleted_counts[type_name] = result.rowcount

        # Delete package record
        await self.db.delete(package)
        await self.db.commit()

        return deleted_counts

    async def list_packages(self) -> list[Package]:
        """List all installed packages."""
        result = await self.db.execute(
            select(Package).order_by(Package.installed_at.desc())
        )
        return list(result.scalars().all())

    async def get_package(self, name: str) -> Optional[Package]:
        """Get a package by name."""
        result = await self.db.execute(
            select(Package).where(Package.name == name)
        )
        return result.scalar_one_or_none()

    async def export_package(self, name: str) -> str:
        """Export a package's original YAML."""
        package = await self.get_package(name)
        if not package:
            raise ValueError(f"Package '{name}' not found")
        return package.source_yaml

    async def create_from_resources(
        self,
        name: str,
        version: str,
        resources: list[dict],
        description: Optional[str] = None,
        author: Optional[str] = None,
        url: Optional[str] = None,
    ) -> str:
        """
        Create a SinasPackage YAML from selected resources.

        Args:
            name: Package name
            version: Package version
            resources: List of {"type": str, "namespace": str, "name": str}
            description: Package description
            author: Package author
            url: Source URL

        Returns:
            YAML string for the SinasPackage
        """
        spec = {}

        # Type -> (model, export_fn)
        type_handlers = {
            "agent": (Agent, self._export_agent),
            "connector": (Connector, self._export_connector),
            "function": (Function, self._export_function),
            "skill": (Skill, self._export_skill),
            "manifest": (Manifest, self._export_manifest),
            "component": (Component, self._export_component),
            "query": (Query, self._export_query),
            "collection": (Collection, self._export_collection),
            "template": (Template, self._export_template),
            "webhook": (Webhook, self._export_webhook),
            "schedule": (ScheduledJob, self._export_schedule),
            "store": (Store, self._export_store),
            "database_trigger": (DatabaseTrigger, self._export_database_trigger),
        }

        for ref in resources:
            res_type = ref["type"]
            res_namespace = ref.get("namespace", "default")
            res_name = ref["name"]

            handler = type_handlers.get(res_type)
            if not handler:
                continue

            model_cls, export_fn = handler
            resource = await self._get_resource(model_cls, res_namespace, res_name)
            if not resource:
                continue

            spec_key = self._type_to_spec_key(res_type)
            if spec_key not in spec:
                spec[spec_key] = []

            exported = await export_fn(resource)
            spec[spec_key].append(exported)

        config_dict = {
            "apiVersion": "sinas.co/v1",
            "kind": "SinasPackage",
            "package": _remove_none_values({
                "name": name,
                "version": version,
                "description": description,
                "author": author,
                "url": url,
            }),
            "spec": spec,
        }

        return yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _type_to_spec_key(self, res_type: str) -> str:
        mapping = {
            "agent": "agents",
            "connector": "connectors",
            "function": "functions",
            "skill": "skills",
            "manifest": "manifests",
            "component": "components",
            "query": "queries",
            "collection": "collections",
            "template": "templates",
            "webhook": "webhooks",
            "schedule": "schedules",
            "store": "stores",
            "database_trigger": "databaseTriggers",
        }
        return mapping.get(res_type, res_type + "s")

    async def _get_resource(self, model_cls, namespace: str, name: str):
        """Get a resource by namespace/name or just name."""
        if hasattr(model_cls, "namespace"):
            stmt = select(model_cls).where(
                model_cls.namespace == namespace,
                model_cls.name == name,
            )
        elif hasattr(model_cls, "path"):
            # Webhook uses path instead of name
            stmt = select(model_cls).where(model_cls.path == name)
        else:
            stmt = select(model_cls).where(model_cls.name == name)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _export_agent(self, agent: Agent) -> dict:
        from app.models.llm_provider import LLMProvider

        provider = None
        if agent.llm_provider_id:
            result = await self.db.execute(
                select(LLMProvider).where(LLMProvider.id == agent.llm_provider_id)
            )
            provider = result.scalar_one_or_none()

        return _remove_none_values({
            "namespace": agent.namespace,
            "name": agent.name,
            "description": agent.description,
            "model": agent.model,
            "llmProviderName": provider.name if provider else None,
            "temperature": agent.temperature,
            "maxTokens": agent.max_tokens,
            "systemPrompt": agent.system_prompt,
            "enabledFunctions": agent.enabled_functions or None,
            "functionParameters": agent.function_parameters or None,
            "enabledAgents": agent.enabled_agents or None,
            "enabledSkills": agent.enabled_skills or None,
            "enabledStores": agent.enabled_stores or None,
            "enabledQueries": agent.enabled_queries or None,
            "queryParameters": agent.query_parameters or None,
            "enabledCollections": agent.enabled_collections or None,
            "enabledComponents": agent.enabled_components or None,
            "icon": agent.icon,
        })

    async def _export_function(self, func: Function) -> dict:
        return _remove_none_values({
            "name": func.name,
            "namespace": func.namespace,
            "description": func.description,
            "code": func.code,
            "inputSchema": func.input_schema,
            "outputSchema": func.output_schema,
            "icon": func.icon,
        })

    async def _export_skill(self, skill: Skill) -> dict:
        return _remove_none_values({
            "namespace": skill.namespace,
            "name": skill.name,
            "description": skill.description,
            "content": skill.content,
        })

    async def _export_manifest(self, manifest: Manifest) -> dict:
        return _remove_none_values({
            "namespace": manifest.namespace,
            "name": manifest.name,
            "description": manifest.description,
            "requiredResources": manifest.required_resources or None,
            "requiredPermissions": manifest.required_permissions or None,
            "optionalPermissions": manifest.optional_permissions or None,
            "exposedNamespaces": manifest.exposed_namespaces or None,
        })

    async def _export_component(self, component: Component) -> dict:
        return _remove_none_values({
            "namespace": component.namespace,
            "name": component.name,
            "title": component.title,
            "description": component.description,
            "sourceCode": component.source_code,
            "inputSchema": component.input_schema,
            "enabledAgents": component.enabled_agents or None,
            "enabledFunctions": component.enabled_functions or None,
            "enabledQueries": component.enabled_queries or None,
            "enabledComponents": component.enabled_components or None,
            "enabledStores": component.enabled_stores or None,
            "cssOverrides": component.css_overrides,
            "visibility": component.visibility,
        })

    async def _export_query(self, query: Query) -> dict:
        from app.models.database_connection import DatabaseConnection

        conn_name = None
        if query.database_connection_id:
            result = await self.db.execute(
                select(DatabaseConnection).where(DatabaseConnection.id == query.database_connection_id)
            )
            conn = result.scalar_one_or_none()
            if conn:
                conn_name = conn.name

        return _remove_none_values({
            "namespace": query.namespace,
            "name": query.name,
            "description": query.description,
            "connectionName": conn_name,
            "operation": query.operation,
            "sql": query.sql,
            "inputSchema": query.input_schema,
            "outputSchema": query.output_schema,
            "timeoutMs": query.timeout_ms,
            "maxRows": query.max_rows,
        })

    async def _export_collection(self, collection: Collection) -> dict:
        return _remove_none_values({
            "namespace": collection.namespace,
            "name": collection.name,
            "metadataSchema": collection.metadata_schema or None,
            "contentFilterFunction": collection.content_filter_function,
            "postUploadFunction": collection.post_upload_function,
            "maxFileSizeMb": collection.max_file_size_mb,
            "maxTotalSizeGb": collection.max_total_size_gb,
            "isPublic": collection.is_public,
            "allowSharedFiles": collection.allow_shared_files,
            "allowPrivateFiles": collection.allow_private_files,
        })

    async def _export_store(self, store: Store) -> dict:
        return _remove_none_values({
            "namespace": store.namespace,
            "name": store.name,
            "description": store.description,
            "schema": store.schema or None,
            "strict": store.strict,
            "defaultVisibility": store.default_visibility,
            "encrypted": store.encrypted,
        })

    async def _export_template(self, template: Template) -> dict:
        return _remove_none_values({
            "namespace": template.namespace,
            "name": template.name,
            "description": template.description,
            "title": template.title,
            "htmlContent": template.html_content,
            "textContent": template.text_content,
            "variableSchema": template.variable_schema if template.variable_schema else None,
        })

    async def _export_webhook(self, webhook: Webhook) -> dict:
        return _remove_none_values({
            "path": webhook.path,
            "functionName": f"{webhook.function_namespace}/{webhook.function_name}",
            "httpMethod": webhook.http_method,
            "requiresAuth": webhook.requires_auth,
            "description": webhook.description,
            "defaultValues": webhook.default_values or None,
        })

    async def _export_schedule(self, schedule: ScheduledJob) -> dict:
        return _remove_none_values({
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
            "inputData": schedule.input_data or None,
        })

    async def _export_database_trigger(self, trigger: DatabaseTrigger) -> dict:
        from app.models.database_connection import DatabaseConnection

        conn_name = None
        if trigger.database_connection_id:
            result = await self.db.execute(
                select(DatabaseConnection).where(
                    DatabaseConnection.id == trigger.database_connection_id
                )
            )
            conn = result.scalar_one_or_none()
            if conn:
                conn_name = conn.name

        return _remove_none_values({
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
        })

    async def _export_connector(self, conn: Connector) -> dict:
        auth = conn.auth or {}
        retry = conn.retry or {}
        operations = []
        for op in (conn.operations or []):
            op_dict = {
                "name": op.get("name"),
                "method": op.get("method"),
                "path": op.get("path"),
                "description": op.get("description"),
                "parameters": op.get("parameters"),
                "requestBodyMapping": op.get("request_body_mapping", "json"),
                "responseMapping": op.get("response_mapping", "json"),
            }
            operations.append(_remove_none_values(op_dict))

        return _remove_none_values({
            "name": conn.name,
            "namespace": conn.namespace,
            "description": conn.description,
            "baseUrl": conn.base_url,
            "auth": _remove_none_values({
                "type": auth.get("type", "none"),
                "secret": auth.get("secret"),
                "header": auth.get("header"),
                "position": auth.get("position"),
                "paramName": auth.get("param_name"),
            }),
            "headers": conn.headers if conn.headers else None,
            "retry": _remove_none_values({
                "maxAttempts": retry.get("max_attempts", 1),
                "backoff": retry.get("backoff", "none"),
            }),
            "timeoutSeconds": conn.timeout_seconds,
            "operations": operations,
        })
