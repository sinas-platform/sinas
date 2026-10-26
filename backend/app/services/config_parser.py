"""
Configuration parser and validator
Validates declarative configuration before application
"""
from typing import Any, Optional

import yaml
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.config import SinasConfig
from app.services.config_schema_errors import schema_errors, strip_legacy_fields


def _is_variable_placeholder(ref: str) -> bool:
    """Check if a value is an unsubstituted ${{ vars.* }} placeholder."""
    return "${{" in ref and "vars." in ref


def _is_wildcard(ref: str) -> bool:
    """Check if a reference contains a wildcard pattern."""
    return "*" in ref


def _ref_matches_any(ref: str, known_names: set[str]) -> bool:
    """Check if a ref matches any known name, supporting wildcards and variable placeholders.

    Wildcards and variable placeholders always pass validation
    (they resolve at runtime).
    """
    if _is_wildcard(ref):
        return True
    if _is_variable_placeholder(ref):
        return True
    return ref in known_names


class ConfigValidationError:
    """Configuration validation error"""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message

    def __str__(self):
        return f"{self.path}: {self.message}"


class ConfigValidation:
    """Configuration validation result"""

    def __init__(self):
        self.errors: list[ConfigValidationError] = []
        self.warnings: list[str] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


class ConfigParser:
    """Parser and validator for SINAS configuration"""

    @staticmethod
    async def parse_and_validate(
        config_yaml: str,
        db: Optional[AsyncSession] = None,
        strict: bool = True,
        extra_names: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[SinasConfig], ConfigValidation]:
        """
        Parse and validate SINAS configuration

        Args:
            config_yaml: YAML configuration string
            db: Optional database session for reference validation
            strict: If True, treat warnings as errors
            extra_names: resources about to exist that references may name,
                by config kind ({"skills": {"gmail/search", ...}}): what the
                packages planned before this one declare, in a preview
                (docs/design/package-dependencies.md)

        Returns:
            Tuple of (parsed config, validation result)
        """
        validation = ConfigValidation()

        # Parse YAML
        try:
            config_dict = yaml.safe_load(config_yaml)
        except yaml.YAMLError as e:
            validation.errors.append(ConfigValidationError("root", f"Invalid YAML: {str(e)}"))
            return None, validation

        # Parse into Pydantic model. Every problem is its own error, naming
        # the resource it is in: an unknown key included (the config models
        # forbid them), which used to be dropped without a word.
        # Fields an older Sinas exported that this one no longer has: a
        # warning, so a 0.4 export (or a file built from one) still applies.
        validation.warnings.extend(strip_legacy_fields(config_dict))
        try:
            config = SinasConfig.model_validate(config_dict)
        except PydanticValidationError as e:
            for path, message in schema_errors(e, config_dict):
                validation.errors.append(ConfigValidationError(path, message))
            return None, validation
        except Exception as e:
            validation.errors.append(
                ConfigValidationError("root", f"Schema validation failed: {str(e)}")
            )
            return None, validation

        # Add warnings for SinasPackage with environment-specific resources
        if config.kind == "SinasPackage":
            if config.spec.users:
                validation.warnings.append(
                    "SinasPackage includes users — these are environment-specific and will be skipped during install"
                )
            if config.spec.llmProviders:
                validation.warnings.append(
                    "SinasPackage includes llmProviders — these are environment-specific and will be skipped during install"
                )
            if config.spec.databaseConnections:
                validation.warnings.append(
                    "SinasPackage includes databaseConnections — these are environment-specific and will be skipped during install"
                )

        # Validate references
        await ConfigParser._validate_references(config, validation, db, extra_names or {})

        return config, validation

    @staticmethod
    async def _validate_references(
        config: SinasConfig,
        validation: ConfigValidation,
        db: Optional[AsyncSession],
        extra_names: Optional[dict[str, Any]] = None,
    ):
        """Validate cross-references within configuration"""
        errors = validation.errors
        spec = (
            config.spec.model_dump() if hasattr(config.spec, "model_dump") else config.spec.dict()
        )
        extra_names = extra_names or {}

        def extra(kind: str) -> set[str]:
            return set(extra_names.get(kind) or ())

        # Collect all role names from config
        role_names = {r["name"] for r in spec.get("roles", [])}
        # Functions and agents use namespace/name format
        function_names = {
            f"{f.get('namespace', 'default')}/{f['name']}" for f in spec.get("functions", [])
        }
        agent_names = {
            f"{a.get('namespace', 'default')}/{a['name']}" for a in spec.get("agents", [])
        }
        skill_names = {
            f"{s.get('namespace', 'default')}/{s['name']}" for s in spec.get("skills", [])
        }
        collection_names = {
            f"{c.get('namespace', 'default')}/{c['name']}" for c in spec.get("collections", [])
        }
        database_connection_names = {c["name"] for c in spec.get("databaseConnections", [])}
        query_names = {
            f"{q.get('namespace', 'default')}/{q['name']}" for q in spec.get("queries", [])
        }
        # Database names (if db provided)
        db_role_names: set[str] = set()
        db_function_names: set[str] = set()
        db_agent_names: set[str] = set()
        db_skill_names: set[str] = set()
        db_collection_names: set[str] = set()
        db_database_connection_names: set[str] = set()
        db_query_names: set[str] = set()
        if db:
            from app.models.agent import Agent
            from app.models.file import Collection
            from app.models.function import Function
            from app.models.skill import Skill
            from app.models.user import Role

            # Load existing resource names from database
            result = await db.execute(select(Role.name))
            db_role_names = {name for (name,) in result.fetchall()}

            # Functions, agents, skills, and collections use namespace/name format
            result = await db.execute(select(Function.namespace, Function.name))
            db_function_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Agent.namespace, Agent.name))
            db_agent_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Skill.namespace, Skill.name))
            db_skill_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Collection.namespace, Collection.name))
            db_collection_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            from app.models.database_connection import DatabaseConnection
            from app.models.query import Query as QueryModel

            result = await db.execute(select(DatabaseConnection.name))
            db_database_connection_names = {name for (name,) in result.fetchall()}

            result = await db.execute(select(QueryModel.namespace, QueryModel.name))
            db_query_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

        component_names = {
            f"{c.get('namespace', 'default')}/{c['name']}" for c in spec.get("components", [])
        }
        db_component_names: set[str] = set()
        if db:
            from app.models.component import Component

            result = await db.execute(select(Component.namespace, Component.name))
            db_component_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

        # Combined sets (config + database + about to exist)
        all_role_names = role_names | db_role_names | extra("roles")
        all_function_names = function_names | db_function_names | extra("functions")
        all_agent_names = agent_names | db_agent_names | extra("agents")
        all_skill_names = skill_names | db_skill_names | extra("skills")
        all_collection_names = collection_names | db_collection_names | extra("collections")
        all_component_names = component_names | db_component_names | extra("components")
        all_database_connection_names = database_connection_names | db_database_connection_names
        all_query_names = query_names | db_query_names | extra("queries")
        # Validate manifest resource references: the resource types the
        # manifest /status check knows (one table, manifest_validator).
        from app.services.manifest_validator import MANIFEST_RESOURCE_TYPES

        valid_resource_types = set(MANIFEST_RESOURCE_TYPES)
        lookup = {
            "agent": all_agent_names,
            "function": all_function_names,
            "skill": all_skill_names,
            "collection": all_collection_names,
            "component": all_component_names,
            "query": all_query_names,
        }

        async def names_of(res_type: str) -> set[str]:
            # Kinds the checks above don't load, looked up only when a
            # manifest asks for one.
            if res_type not in lookup:
                model, kind = MANIFEST_RESOURCE_TYPES[res_type]
                names = {
                    f"{c.get('namespace', 'default')}/{c['name']}" for c in spec.get(kind, [])
                } | extra(kind)
                if db:
                    result = await db.execute(select(model.namespace, model.name))
                    names |= {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}
                lookup[res_type] = names
            return lookup[res_type]

        for i, manifest in enumerate(spec.get("manifests", [])):
            for j, res in enumerate(manifest.get("requiredResources", [])):
                res_type = res.get("type", "")
                if res_type not in valid_resource_types:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.manifests[{i}].requiredResources[{j}].type",
                            message=f"Unsupported resource type '{res_type}'. Must be one of: {', '.join(sorted(valid_resource_types))}",
                        )
                    )
                    continue

                res_ns = res.get("namespace", "default")
                res_name = res.get("name", "")
                res_ref = f"{res_ns}/{res_name}"

                # Check if the referenced resource exists in config or DB
                if res_ref not in await names_of(res_type):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.manifests[{i}].requiredResources[{j}]",
                            message=f"Referenced {res_type} '{res_ref}' not defined",
                        )
                    )

        # Agent references are checked by the agent applier, on every channel,
        # for what a write adds: create the agent, its tools, then add them.
        # Checking every listed one here blocked editing an agent that still
        # holds an older reference to something since removed.

        # Validate component references
        for i, comp in enumerate(spec.get("components", [])):
            # Validate enabled function references
            if comp.get("enabledFunctions"):
                for func_ref in comp["enabledFunctions"]:
                    if not _ref_matches_any(func_ref, all_function_names):
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.components[{i}].enabledFunctions",
                                message=f"Referenced function '{func_ref}' not defined",
                            )
                        )
            # Validate enabled query references
            if comp.get("enabledQueries"):
                for query_ref in comp["enabledQueries"]:
                    if not _ref_matches_any(query_ref, all_query_names):
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.components[{i}].enabledQueries",
                                message=f"Referenced query '{query_ref}' not defined",
                            )
                        )
            # Validate enabled agent references
            if comp.get("enabledAgents"):
                for agent_ref in comp["enabledAgents"]:
                    if not _ref_matches_any(agent_ref, all_agent_names):
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.components[{i}].enabledAgents",
                                message=f"Referenced agent '{agent_ref}' not defined",
                            )
                        )
            # Validate enabled component references
            if comp.get("enabledComponents"):
                for comp_ref in comp["enabledComponents"]:
                    if not _ref_matches_any(comp_ref, all_component_names):
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.components[{i}].enabledComponents",
                                message=f"Referenced component '{comp_ref}' not defined",
                            )
                        )

        # Validate query references
        for i, query in enumerate(spec.get("queries", [])):
            if not _is_variable_placeholder(query.get("connectionName", "")) and query["connectionName"] not in all_database_connection_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.queries[{i}].connectionName",
                        message=f"Referenced database connection '{query['connectionName']}' not defined",
                    )
                )

        # Validate collection references
        for i, coll in enumerate(spec.get("collections", [])):
            # Validate content filter function reference
            if coll.get("contentFilterFunction"):
                if not _is_variable_placeholder(coll["contentFilterFunction"]) and coll["contentFilterFunction"] not in all_function_names:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.collections[{i}].contentFilterFunction",
                            message=f"Referenced function '{coll['contentFilterFunction']}' not defined",
                        )
                    )
            # Validate post-upload function reference
            if coll.get("postUploadFunction"):
                if not _is_variable_placeholder(coll["postUploadFunction"]) and coll["postUploadFunction"] not in all_function_names:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.collections[{i}].postUploadFunction",
                            message=f"Referenced function '{coll['postUploadFunction']}' not defined",
                        )
                    )

        # Pipeline names (config + database) for webhook/schedule/trigger target checks
        pipeline_names = {
            f"{p.get('namespace', 'default')}/{p['name']}" for p in spec.get("pipelines", [])
        }
        db_pipeline_names: set[str] = set()
        if db:
            from app.models.pipeline import Pipeline as PipelineModel

            result = await db.execute(select(PipelineModel.namespace, PipelineModel.name))
            db_pipeline_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}
        all_pipeline_names = pipeline_names | db_pipeline_names | extra("pipelines")

        # Validate webhook references
        for i, webhook in enumerate(spec.get("webhooks", [])):
            webhook_target = webhook.get("targetType", "function")
            if webhook_target == "pipeline":
                pipeline_ref = webhook.get("pipelineName") or ""
                if "/" not in pipeline_ref:
                    pipeline_ref = f"default/{pipeline_ref}"
                if not _ref_matches_any(pipeline_ref, all_pipeline_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.webhooks[{i}].pipelineName",
                            message=f"Referenced pipeline '{pipeline_ref}' not defined",
                        )
                    )
            elif webhook_target == "agent":
                agent_ref = webhook.get("agentName") or ""
                if "/" not in agent_ref:
                    agent_ref = f"default/{agent_ref}"
                if not _ref_matches_any(agent_ref, all_agent_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.webhooks[{i}].agentName",
                            message=f"Referenced agent '{agent_ref}' not defined",
                        )
                    )
            else:
                # functionName may be "namespace/name" or a bare name
                func_ref = webhook.get("functionName") or ""
                if "/" not in func_ref:
                    func_ref = f"{webhook.get('functionNamespace', 'default')}/{func_ref}"
                if not _ref_matches_any(func_ref, all_function_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.webhooks[{i}].functionName",
                            message=f"Referenced function '{func_ref}' not defined",
                        )
                    )

        # Validate schedule references (target depends on scheduleType)
        for i, schedule in enumerate(spec.get("schedules", [])):
            schedule_type = schedule.get("scheduleType", "function")
            if schedule_type == "pipeline":
                pipeline_ref = schedule.get("pipelineName") or ""
                if "/" not in pipeline_ref:
                    pipeline_ref = f"default/{pipeline_ref}"
                if not _ref_matches_any(pipeline_ref, all_pipeline_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.schedules[{i}].pipelineName",
                            message=f"Referenced pipeline '{pipeline_ref}' not defined",
                        )
                    )
            elif schedule_type == "agent":
                agent_ref = schedule.get("agentName") or ""
                if "/" not in agent_ref:
                    agent_ref = f"default/{agent_ref}"
                if not _ref_matches_any(agent_ref, all_agent_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.schedules[{i}].agentName",
                            message=f"Referenced agent '{agent_ref}' not defined",
                        )
                    )
            else:
                # functionName may be "namespace/name" or a bare name
                func_ref = schedule.get("functionName") or ""
                if "/" not in func_ref:
                    func_ref = f"{schedule.get('functionNamespace', 'default')}/{func_ref}"
                if not _ref_matches_any(func_ref, all_function_names):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.schedules[{i}].functionName",
                            message=f"Referenced function '{func_ref}' not defined",
                        )
                    )

