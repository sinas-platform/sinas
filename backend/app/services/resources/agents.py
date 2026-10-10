"""Agents applier.

`is_active` is a soft delete: deleting an agent through the API switches it
off (recorded), and the API can switch it back on. Config and packages leave
that state alone unless they declare isActive. A package uninstall (or an
upgrade that drops one) does delete the row; its chats are kept, unlinked.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select, update

from app.models.agent import Agent
from app.models.llm_provider import LLMProvider
from app.schemas.spec.agent import AgentSpec
from app.services.resources.base import ApplyContext, ReferenceNotFound, ResourceApplier

_FIELDS = (
    "description", "model", "max_tokens", "system_prompt", "icon", "default_job_timeout",
    "default_keep_alive", "is_active", "temperature",
)
_LISTS = (
    "enabled_functions", "enabled_agents", "enabled_queries", "enabled_components",
    "enabled_connectors", "enabled_pipelines", "system_tools",
)
_MAPS = ("input_schema", "output_schema", "function_parameters", "status_templates", "query_parameters")


class AgentApplier(ResourceApplier[AgentSpec]):
    kind = "agents"
    label = "Agent"
    noun = "agent"
    config_section = "agents"
    spec_model = AgentSpec
    model = Agent
    reference_fields = ("llm_provider_name",)
    # Operator state: a deleted (switched-off) agent stays off, and the
    # default chosen in the console stays the default, unless config says so.
    keep_unless_declared = ("is_active", "is_default")

    def key_of(self, spec: AgentSpec) -> str:
        return spec.key

    def key_of_row(self, row: Agent) -> str:
        return f"{row.namespace}/{row.name}"

    def config_key(self, item: Any) -> str:
        return f"{item.namespace}/{item.name}"

    async def find(self, ctx: ApplyContext, key: str) -> Agent | None:
        # Deleted (inactive) ones too: they still hold the name.
        namespace, name = key.split("/", 1)
        return (
            await ctx.db.execute(
                select(Agent)
                .where(Agent.namespace == namespace, Agent.name == name)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    def spec_from_row(self, row: Agent, provider_name: Optional[str] = None) -> AgentSpec:
        from app.schemas.spec.agent import CollectionRef, SkillRef, StoreRef

        return AgentSpec.model_construct(
            namespace=row.namespace,
            name=row.name,
            description=row.description or None,
            llm_provider_name=provider_name,
            model=row.model or None,
            provider_overrides=dict(row.provider_overrides) if row.provider_overrides else None,
            temperature=0.7 if row.temperature is None else row.temperature,
            max_tokens=row.max_tokens,
            system_prompt=row.system_prompt or None,
            input_schema=dict(row.input_schema or {}),
            output_schema=dict(row.output_schema or {}),
            initial_messages=list(row.initial_messages) if row.initial_messages is not None else None,
            enabled_functions=list(row.enabled_functions or []),
            function_parameters=dict(row.function_parameters or {}),
            status_templates=dict(row.status_templates or {}),
            enabled_agents=list(row.enabled_agents or []),
            enabled_skills=[
                SkillRef.model_construct(**{"preload": False, **s}) for s in row.enabled_skills or []
                if isinstance(s, dict)
            ],
            enabled_stores=[
                StoreRef.model_construct(**{"access": "readonly", **s}) for s in row.enabled_stores or []
                if isinstance(s, dict)
            ],
            enabled_queries=list(row.enabled_queries or []),
            query_parameters=dict(row.query_parameters or {}),
            enabled_collections=[
                CollectionRef.model_construct(**{"access": "readonly", **c})
                for c in row.enabled_collections or [] if isinstance(c, dict)
            ],
            enabled_components=list(row.enabled_components or []),
            enabled_connectors=list(row.enabled_connectors or []),
            enabled_pipelines=list(row.enabled_pipelines or []),
            hooks=dict(row.hooks) if row.hooks else None,
            icon=row.icon or None,
            default_job_timeout=row.default_job_timeout,
            default_keep_alive=bool(row.default_keep_alive),
            system_tools=list(row.system_tools or []),
            is_default=bool(row.is_default),
            is_active=row.is_active is not False,
        )

    async def current_spec(self, ctx: ApplyContext, row: Agent) -> AgentSpec:
        return self.spec_from_row(row, await provider_name(ctx.db, row.llm_provider_id))

    def new_row(self, spec: AgentSpec, ctx: ApplyContext) -> Agent:
        owner = ctx.owner_user_id
        return Agent(user_id=uuid.UUID(str(owner)) if owner else None)

    async def _provider_id(self, ctx: ApplyContext, name: str) -> uuid.UUID:
        provider_id = (
            await ctx.db.execute(select(LLMProvider.id).where(LLMProvider.name == name))
        ).scalar_one_or_none()
        if provider_id is None:
            raise ReferenceNotFound(f"LLM provider '{name}' not found")
        return provider_id

    async def write_row(
        self, row: Agent, spec: AgentSpec, ctx: ApplyContext, current: Optional[AgentSpec] = None
    ) -> None:
        # The provider is held by id; resolve the name only when the spec
        # names another provider than the one the change was computed against.
        if current is None or current.llm_provider_name != spec.llm_provider_name:
            row.llm_provider_id = (
                await self._provider_id(ctx, spec.llm_provider_name) if spec.llm_provider_name else None
            )
        # One default agent: making this one the default unsets the others.
        if spec.is_default and not (current is not None and current.is_default):
            stmt = update(Agent).where(Agent.is_default.is_(True)).values(is_default=False)
            if row.id is not None:
                stmt = stmt.where(Agent.id != row.id)
            await ctx.db.execute(stmt)
        row.namespace = spec.namespace
        row.name = spec.name
        for field in _FIELDS:
            setattr(row, field, getattr(spec, field))
        for field in _LISTS:
            setattr(row, field, list(getattr(spec, field)))
        for field in _MAPS:
            setattr(row, field, dict(getattr(spec, field)))
        row.provider_overrides = dict(spec.provider_overrides) if spec.provider_overrides else None
        row.initial_messages = list(spec.initial_messages) if spec.initial_messages is not None else None
        row.enabled_skills = [s.model_dump() for s in spec.enabled_skills]
        row.enabled_stores = [s.model_dump() for s in spec.enabled_stores]
        row.enabled_collections = [c.model_dump() for c in spec.enabled_collections]
        row.hooks = dict(spec.hooks) if spec.hooks is not None else None
        row.is_default = spec.is_default

    async def check_references(self, spec: AgentSpec, ctx: ApplyContext) -> None:
        # A preview accepts a provider the same config declares.
        if spec.llm_provider_name and not ctx.declared("llmProviders", spec.llm_provider_name):
            await self._provider_id(ctx, spec.llm_provider_name)

    async def delete(self, row: Agent, ctx: ApplyContext) -> None:
        """Hard delete (package uninstall, or an upgrade that drops it). Chats
        outlive it: a conversation is the user's, so only their link to the
        agent is cleared (they keep its namespace/name)."""
        if not ctx.dry_run:
            from app.models.chat import Chat

            await ctx.db.execute(update(Chat).where(Chat.agent_id == row.id).values(agent_id=None))
        await super().delete(row, ctx)


async def provider_name(db, provider_id) -> Optional[str]:
    if provider_id is None:
        return None
    return (
        await db.execute(select(LLMProvider.name).where(LLMProvider.id == provider_id))
    ).scalar_one_or_none()
