"""
Agent applier
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.llm_provider import LLMProvider

from app.services.config_apply.normalizers import (
    normalize_collection_references,
    normalize_function_references,
    normalize_skill_references,
    normalize_store_references,
    should_skip_existing,
)

logger = logging.getLogger(__name__)


def _validated_overrides(agent_config, errors: list[str]):
    """Whitelist-check providerOverrides; invalid entries become apply errors
    (same fail-loudly posture as unresolvable provider names)."""
    overrides = getattr(agent_config, "providerOverrides", None)
    if not overrides:
        return None
    from app.providers.factory import validate_provider_overrides

    problems = validate_provider_overrides(overrides)
    if problems:
        errors.extend(
            f"Agent '{agent_config.namespace}/{agent_config.name}': {p}"
            for p in problems
        )
        return None
    return overrides


async def _resolve_llm_provider_id(
    db: AsyncSession,
    provider_name: str | None,
    llm_provider_ids: dict[str, str],
    agent_label: str,
) -> str | None:
    """
    Resolve an agent's ``llmProviderName`` to a provider id.

    Prefers config-declared providers, then falls back to a provider that
    already exists in the DB (e.g. created via the API/console). Returns ``None``
    only when no provider name is set. Raises ``ValueError`` when a name IS set
    but cannot be resolved, so we fail loudly instead of silently routing the
    agent to the default provider.
    """
    if not provider_name:
        return None

    provider_id = llm_provider_ids.get(provider_name)
    if provider_id:
        return provider_id

    result = await db.execute(
        select(LLMProvider.id).where(LLMProvider.name == provider_name)
    )
    existing_id = result.scalar_one_or_none()
    if existing_id:
        return str(existing_id)

    raise ValueError(
        f"Agent '{agent_label}' references LLM provider '{provider_name}', "
        "which is neither declared in config nor present in the database"
    )


async def apply_agents(
    db: AsyncSession,
    agents: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
    llm_provider_ids: dict[str, str],
    agent_ids: dict[str, str],
) -> None:
    """Apply agent configurations"""
    for agent_config in agents:
        try:
            stmt = select(Agent).where(
                Agent.namespace == agent_config.namespace, Agent.name == agent_config.name
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            # Normalize function references to namespace/name format
            normalized_functions = (
                normalize_function_references(agent_config.enabledFunctions)
                if agent_config.enabledFunctions
                else []
            )

            # Normalize skill references to dict format
            normalized_skills = (
                normalize_skill_references(agent_config.enabledSkills)
                if agent_config.enabledSkills
                else []
            )

            # Normalize store references to dict format
            normalized_stores = (
                normalize_store_references(agent_config.enabledStores)
                if agent_config.enabledStores
                else []
            )

            # Normalize collection references to dict format
            normalized_collections = (
                normalize_collection_references(agent_config.enabledCollections)
                if agent_config.enabledCollections
                else []
            )

            config_hash = calculate_hash(
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
                    "enabled_collections": sorted(normalized_collections, key=lambda x: x["collection"])
                    if normalized_collections
                    else [],
                    "enabled_components": sorted(agent_config.enabledComponents)
                    if agent_config.enabledComponents
                    else [],
                    "enabled_connectors": agent_config.enabledConnectors
                    if agent_config.enabledConnectors
                    else [],
                    "enabled_pipelines": sorted(agent_config.enabledPipelines)
                    if agent_config.enabledPipelines
                    else [],
                    "input_schema": agent_config.inputSchema or {},
                    "output_schema": agent_config.outputSchema or {},
                    "initial_messages": agent_config.initialMessages or [],
                    "hooks": agent_config.hooks,
                    "icon": agent_config.icon,
                    "is_default": agent_config.isDefault,
                    "default_job_timeout": agent_config.defaultJobTimeout,
                    "default_keep_alive": agent_config.defaultKeepAlive,
                    "system_tools": agent_config.systemTools,
                }
            )

            if existing:
                if should_skip_existing(
                    existing, managed_by, config_name, config_hash,
                    "agents", f"{agent_config.namespace}/{agent_config.name}",
                    track_change, warnings,
                ):
                    agent_ids[agent_config.name] = str(existing.id)
                    continue
                    track_change("unchanged", "agents", f"{agent_config.namespace}/{agent_config.name}")
                    agent_ids[agent_config.name] = str(existing.id)
                    continue

                if not dry_run:
                    # Resolve LLM provider (None if not specified = use default;
                    # fails loudly if a named provider can't be resolved).
                    existing.llm_provider_id = await _resolve_llm_provider_id(
                        db,
                        agent_config.llmProviderName,
                        llm_provider_ids,
                        f"{agent_config.namespace}/{agent_config.name}",
                    )

                    existing.description = agent_config.description
                    existing.model = agent_config.model
                    existing.provider_overrides = _validated_overrides(
                        agent_config, errors
                    )
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
                    existing.enabled_collections = normalized_collections
                    existing.enabled_components = agent_config.enabledComponents
                    existing.enabled_connectors = agent_config.enabledConnectors
                    existing.enabled_pipelines = agent_config.enabledPipelines
                    existing.input_schema = agent_config.inputSchema or {}
                    existing.output_schema = agent_config.outputSchema or {}
                    existing.initial_messages = agent_config.initialMessages
                    existing.hooks = agent_config.hooks
                    existing.icon = agent_config.icon
                    existing.default_job_timeout = agent_config.defaultJobTimeout
                    existing.default_keep_alive = agent_config.defaultKeepAlive
                    existing.system_tools = agent_config.systemTools
                    if agent_config.isDefault:
                        await db.execute(
                            Agent.__table__.update()
                            .where(Agent.id != existing.id)
                            .values(is_default=False)
                        )
                    existing.is_default = agent_config.isDefault
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "agents", f"{agent_config.namespace}/{agent_config.name}")
                agent_ids[agent_config.name] = str(existing.id)

            else:
                if not dry_run:
                    # Resolve LLM provider (None if not specified = use default;
                    # fails loudly if a named provider can't be resolved).
                    llm_provider_id = await _resolve_llm_provider_id(
                        db,
                        agent_config.llmProviderName,
                        llm_provider_ids,
                        f"{agent_config.namespace}/{agent_config.name}",
                    )

                    if agent_config.isDefault:
                        await db.execute(
                            Agent.__table__.update().values(is_default=False)
                        )

                    new_agent = Agent(
                        namespace=agent_config.namespace,
                        name=agent_config.name,
                        description=agent_config.description,
                        llm_provider_id=llm_provider_id,
                        model=agent_config.model,
                        provider_overrides=_validated_overrides(agent_config, errors),
                        temperature=agent_config.temperature,
                        max_tokens=agent_config.maxTokens,
                        system_prompt=agent_config.systemPrompt,
                        input_schema=agent_config.inputSchema or {},
                        output_schema=agent_config.outputSchema or {},
                        initial_messages=agent_config.initialMessages,
                        enabled_functions=normalized_functions,
                        function_parameters=agent_config.functionParameters,
                        status_templates=agent_config.statusTemplates,
                        enabled_agents=agent_config.enabledAgents,
                        enabled_skills=normalized_skills,
                        enabled_stores=normalized_stores,
                        enabled_queries=agent_config.enabledQueries,
                        query_parameters=agent_config.queryParameters,
                        enabled_collections=normalized_collections,
                        enabled_components=agent_config.enabledComponents,
                        enabled_connectors=agent_config.enabledConnectors,
                        enabled_pipelines=agent_config.enabledPipelines,
                        hooks=agent_config.hooks,
                        icon=agent_config.icon,
                        is_default=agent_config.isDefault,
                        default_job_timeout=agent_config.defaultJobTimeout,
                        default_keep_alive=agent_config.defaultKeepAlive,
                        system_tools=agent_config.systemTools,
                        user_id=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_agent)
                    await db.flush()
                    agent_ids[agent_config.name] = str(new_agent.id)
                else:
                    agent_ids[agent_config.name] = "dry-run-id"

                track_change("create", "agents", f"{agent_config.namespace}/{agent_config.name}")

        except Exception as e:
            errors.append(f"Error applying agent '{agent_config.name}': {str(e)}")
