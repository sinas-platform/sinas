"""
Configuration parser and validator
Validates declarative configuration before application
"""
from typing import Optional

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.config import SinasConfig


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
        config_yaml: str, db: Optional[AsyncSession] = None, strict: bool = True
    ) -> tuple[Optional[SinasConfig], ConfigValidation]:
        """
        Parse and validate SINAS configuration

        Args:
            config_yaml: YAML configuration string
            db: Optional database session for reference validation
            strict: If True, treat warnings as errors

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

        # Parse into Pydantic model
        try:
            config = SinasConfig(**config_dict)
        except Exception as e:
            validation.errors.append(
                ConfigValidationError("root", f"Schema validation failed: {str(e)}")
            )
            return None, validation

        # Validate references
        await ConfigParser._validate_references(config, validation, db)

        return config, validation

    @staticmethod
    async def _validate_references(
        config: SinasConfig, validation: ConfigValidation, db: Optional[AsyncSession]
    ):
        """Validate cross-references within configuration"""
        errors = validation.errors
        spec = (
            config.spec.model_dump() if hasattr(config.spec, "model_dump") else config.spec.dict()
        )

        # Collect all group names from config
        group_names = {g["name"] for g in spec.get("groups", [])}
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
        llm_provider_names = {p["name"] for p in spec.get("llmProviders", [])}
        database_connection_names = {c["name"] for c in spec.get("databaseConnections", [])}
        query_names = {
            f"{q.get('namespace', 'default')}/{q['name']}" for q in spec.get("queries", [])
        }
        # Database names (if db provided)
        db_group_names: set[str] = set()
        db_function_names: set[str] = set()
        db_agent_names: set[str] = set()
        db_skill_names: set[str] = set()
        db_collection_names: set[str] = set()
        db_llm_provider_names: set[str] = set()
        db_database_connection_names: set[str] = set()
        db_query_names: set[str] = set()
        if db:
            from app.models.agent import Agent
            from app.models.file import Collection
            from app.models.function import Function
            from app.models.llm_provider import LLMProvider
            from app.models.skill import Skill
            from app.models.user import Role

            # Load existing resource names from database
            result = await db.execute(select(Role.name))
            db_group_names = {name for (name,) in result.fetchall()}

            # Functions, agents, skills, and collections use namespace/name format
            result = await db.execute(select(Function.namespace, Function.name))
            db_function_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Agent.namespace, Agent.name))
            db_agent_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Skill.namespace, Skill.name))
            db_skill_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(Collection.namespace, Collection.name))
            db_collection_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

            result = await db.execute(select(LLMProvider.name))
            db_llm_provider_names = {name for (name,) in result.fetchall()}

            from app.models.database_connection import DatabaseConnection
            from app.models.query import Query as QueryModel

            result = await db.execute(select(DatabaseConnection.name))
            db_database_connection_names = {name for (name,) in result.fetchall()}

            result = await db.execute(select(QueryModel.namespace, QueryModel.name))
            db_query_names = {f"{namespace}/{name}" for (namespace, name) in result.fetchall()}

        # Combined sets (config + database)
        all_group_names = group_names | db_group_names
        all_function_names = function_names | db_function_names
        all_agent_names = agent_names | db_agent_names
        all_skill_names = skill_names | db_skill_names
        all_collection_names = collection_names | db_collection_names
        all_llm_provider_names = llm_provider_names | db_llm_provider_names
        all_database_connection_names = database_connection_names | db_database_connection_names
        all_query_names = query_names | db_query_names
        # Validate app resource references
        valid_resource_types = {"agent", "function", "skill", "collection"}
        for i, app in enumerate(spec.get("apps", [])):
            for j, res in enumerate(app.get("requiredResources", [])):
                res_type = res.get("type", "")
                if res_type not in valid_resource_types:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.apps[{i}].requiredResources[{j}].type",
                            message=f"Unsupported resource type '{res_type}'. Must be one of: {', '.join(sorted(valid_resource_types))}",
                        )
                    )
                    continue

                res_ns = res.get("namespace", "default")
                res_name = res.get("name", "")
                res_ref = f"{res_ns}/{res_name}"

                # Check if the referenced resource exists in config or DB
                lookup = {
                    "agent": all_agent_names,
                    "function": all_function_names,
                    "skill": all_skill_names,
                    "collection": all_collection_names,
                }
                if res_ref not in lookup.get(res_type, set()):
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.apps[{i}].requiredResources[{j}]",
                            message=f"Referenced {res_type} '{res_ref}' not defined",
                        )
                    )

        # Validate function references
        for i, func in enumerate(spec.get("functions", [])):
            if func["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.functions[{i}].groupName",
                        message=f"Referenced group '{func['groupName']}' not defined",
                    )
                )

        # Validate agent references
        for i, agent in enumerate(spec.get("agents", [])):
            if agent["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.agents[{i}].groupName",
                        message=f"Referenced group '{agent['groupName']}' not defined",
                    )
                )

            # Validate LLM provider reference
            if "llmProviderName" in agent and agent["llmProviderName"]:
                if agent["llmProviderName"] not in all_llm_provider_names:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.agents[{i}].llmProviderName",
                            message=f"Referenced LLM provider '{agent['llmProviderName']}' not defined",
                        )
                    )

            # Validate enabled function references
            if "enabledFunctions" in agent and agent["enabledFunctions"]:
                for func_name in agent["enabledFunctions"]:
                    if func_name not in all_function_names:
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.agents[{i}].enabledFunctions",
                                message=f"Referenced function '{func_name}' not defined",
                            )
                        )

            # Validate enabled agent references (for agent calling)
            if "enabledAgents" in agent and agent["enabledAgents"]:
                # Build current agent's full name for self-reference check
                current_agent_ref = f"{agent.get('namespace', 'default')}/{agent['name']}"
                for agent_ref in agent["enabledAgents"]:
                    if agent_ref not in all_agent_names and agent_ref != current_agent_ref:
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.agents[{i}].enabledAgents",
                                message=f"Referenced agent '{agent_ref}' not defined",
                            )
                        )

            # Validate enabled skill references
            if "enabledSkills" in agent and agent["enabledSkills"]:
                for skill_ref in agent["enabledSkills"]:
                    if skill_ref not in all_skill_names:
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.agents[{i}].enabledSkills",
                                message=f"Referenced skill '{skill_ref}' not defined",
                            )
                        )

            # Validate enabled query references
            if "enabledQueries" in agent and agent["enabledQueries"]:
                for query_ref in agent["enabledQueries"]:
                    if query_ref not in all_query_names:
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.agents[{i}].enabledQueries",
                                message=f"Referenced query '{query_ref}' not defined",
                            )
                        )

            # Validate enabled collection references
            if "enabledCollections" in agent and agent["enabledCollections"]:
                for coll_ref in agent["enabledCollections"]:
                    if coll_ref not in all_collection_names:
                        errors.append(
                            ConfigValidationError(
                                path=f"spec.agents[{i}].enabledCollections",
                                message=f"Referenced collection '{coll_ref}' not defined",
                            )
                        )

        # Validate query references
        for i, query in enumerate(spec.get("queries", [])):
            if query["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.queries[{i}].groupName",
                        message=f"Referenced group '{query['groupName']}' not defined",
                    )
                )
            if query["connectionName"] not in all_database_connection_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.queries[{i}].connectionName",
                        message=f"Referenced database connection '{query['connectionName']}' not defined",
                    )
                )

        # Validate collection references
        for i, coll in enumerate(spec.get("collections", [])):
            if coll["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.collections[{i}].groupName",
                        message=f"Referenced group '{coll['groupName']}' not defined",
                    )
                )
            # Validate content filter function reference
            if coll.get("contentFilterFunction"):
                if coll["contentFilterFunction"] not in all_function_names:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.collections[{i}].contentFilterFunction",
                            message=f"Referenced function '{coll['contentFilterFunction']}' not defined",
                        )
                    )
            # Validate post-upload function reference
            if coll.get("postUploadFunction"):
                if coll["postUploadFunction"] not in all_function_names:
                    errors.append(
                        ConfigValidationError(
                            path=f"spec.collections[{i}].postUploadFunction",
                            message=f"Referenced function '{coll['postUploadFunction']}' not defined",
                        )
                    )

        # Validate webhook references
        for i, webhook in enumerate(spec.get("webhooks", [])):
            if webhook["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.webhooks[{i}].groupName",
                        message=f"Referenced group '{webhook['groupName']}' not defined",
                    )
                )
            # Build function reference as namespace/name
            func_namespace = webhook.get("functionNamespace", "default")
            func_name = webhook["functionName"]
            func_ref = f"{func_namespace}/{func_name}"
            if func_ref not in all_function_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.webhooks[{i}].functionName",
                        message=f"Referenced function '{func_ref}' not defined",
                    )
                )

        # Validate schedule references
        for i, schedule in enumerate(spec.get("schedules", [])):
            if schedule["groupName"] not in all_group_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.schedules[{i}].groupName",
                        message=f"Referenced group '{schedule['groupName']}' not defined",
                    )
                )
            # Build function reference as namespace/name
            func_namespace = schedule.get("functionNamespace", "default")
            func_name = schedule["functionName"]
            func_ref = f"{func_namespace}/{func_name}"
            if func_ref not in all_function_names:
                errors.append(
                    ConfigValidationError(
                        path=f"spec.schedules[{i}].functionName",
                        message=f"Referenced function '{func_ref}' not defined",
                    )
                )

