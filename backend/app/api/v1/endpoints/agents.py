"""Agent endpoints.

Writes go through AgentApplier, the path config apply and package install
use too: the same validation, ownership and change history on every channel.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    get_current_user_with_permissions,
    set_permission_used,
)
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
)
from app.services.icon_resolver import resolve_icon_url
from app.services.resources import rest
from app.services.resources.agents import AgentApplier, provider_name
from app.services.resources.base import lock_singleton

router = APIRouter()

_applier = AgentApplier()


async def _provider_name(provider_id: Optional[uuid.UUID], db: AsyncSession) -> Optional[str]:
    """The REST API names the provider by id; the spec holds its name."""
    if provider_id is None:
        return None
    name = await provider_name(db, provider_id)
    if name is None:
        raise HTTPException(status_code=404, detail="LLM provider not found")
    return name


def _same_provider(agent: Agent, requested: Optional[uuid.UUID]) -> None:
    # Resolved by name: a provider renamed in between could have given
    # another id than the one requested.
    if agent.llm_provider_id != requested:
        raise HTTPException(status_code=409, detail="The LLM provider changed while saving; try again")


async def _any_state(db: AsyncSession, namespace: str, name: str, user_id=None) -> Optional[Agent]:
    stmt = select(Agent).where(Agent.namespace == namespace, Agent.name == name)
    if user_id is not None:
        stmt = stmt.where(Agent.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _agent_response(agent: Agent, db: AsyncSession) -> AgentResponse:
    """Build AgentResponse with resolved icon_url."""
    resp = AgentResponse.model_validate(agent)
    resp.icon_url = await resolve_icon_url(agent.icon, db)
    return resp


# Agent endpoints


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    req: Request,
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new agent."""
    user_id, permissions = current_user_data

    # Check create permission
    create_perm = "sinas.agents.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(req, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create agents")
    set_permission_used(req, create_perm)

    ctx = rest.api_context(db, user_id)
    if agent_data.is_default:
        # Before any row lock (lock order: see lock_singleton).
        await lock_singleton(ctx, _applier.singleton_lock)
    key = f"{agent_data.namespace}/{agent_data.name}"
    existing = await _applier.find(ctx, key)
    if existing is not None and not existing.is_active:
        # Deleting an agent switches it off; its name stays taken.
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{key}' exists but was deleted. Restore it (PUT with "
            "is_active: true) or choose another name.",
        )
    data = agent_data.model_dump(exclude={"llm_provider_id"})
    data["llm_provider_name"] = await _provider_name(agent_data.llm_provider_id, db)
    # A clash is a 400 "Agent 'ns/name' already exists", as before.
    result = await rest.write(_applier, ctx, rest.parse_spec(_applier, data), must_create=True)
    _same_provider(result.obj, agent_data.llm_provider_id)
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return await _agent_response(result.obj, db)


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """List all agents accessible by the current user."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware filtering
    agents = await Agent.list_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        additional_filters=Agent.is_active == True,
    )

    set_permission_used(req, "sinas.agents.read")

    return [await _agent_response(agent, db) for agent in agents]


@router.get("/{namespace}/{name}", response_model=AgentResponse)
async def get_agent(
    req: Request,
    namespace: str,
    name: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific agent by namespace and name."""
    user_id, permissions = current_user_data

    # Use mixin for permission-aware get (handles 404 and 403 automatically)
    agent = await Agent.get_with_permissions(
        db=db,
        user_id=user_id,
        permissions=permissions,
        action="read",
        namespace=namespace,
        name=name,
    )

    # Soft-deleted agents should appear as not found
    if not agent.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{namespace}/{name}' not found",
        )

    set_permission_used(req, f"sinas.agents/{namespace}/{name}.read")

    return await _agent_response(agent, db)


@router.put("/{namespace}/{name}", response_model=AgentResponse)
async def update_agent(
    req: Request,
    namespace: str,
    name: str,
    agent_data: AgentUpdate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Update an agent."""
    user_id, permissions = current_user_data

    # Check permissions first to determine query scope
    has_all_permission = check_permission(permissions, "sinas.agents.update:all")

    # Deleted (switched-off) agents too: an update with is_active: true
    # restores one.
    if has_all_permission:
        # Admin can update all agents - don't filter by user_id
        agent = await _any_state(db, namespace, name, user_id=None)
        set_permission_used(req, "sinas.agents.update:all")
    else:
        # Regular user - filter by user_id
        agent = await _any_state(db, namespace, name, user_id=user_id)
        set_permission_used(req, f"sinas.agents/{namespace}/{name}.update:own")

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{namespace}/{name}' not found"
        )

    # Additional ownership check for non-admin users (user_id from the token is
    # a str; agent.user_id is a UUID — compare as strings)
    if not has_all_permission and str(agent.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized to update this agent")

    ctx = rest.api_context(db, user_id)
    if agent_data.is_default:
        # Before any row lock (lock order: see lock_singleton).
        await lock_singleton(ctx, _applier.singleton_lock)
    agent = await rest.locked(_applier, ctx, agent)
    # As before: a field left out or sent as null stays as it is; {} clears
    # provider_overrides; a new namespace/name renames it (a clash is a 400).
    patch = {
        field: value
        for field, value in agent_data.model_dump(exclude_unset=True).items()
        if value is not None and field != "llm_provider_id"
    }
    requested = agent_data.llm_provider_id
    if requested is not None and requested != agent.llm_provider_id:
        patch["llm_provider_name"] = await _provider_name(requested, db)
    current = _applier.spec_from_row(agent, await provider_name(db, agent.llm_provider_id))
    spec = rest.patch_spec(_applier, agent, patch, current=current)
    await rest.write(_applier, ctx, spec, existing=agent)
    _same_provider(agent, requested if requested is not None else agent.llm_provider_id)
    await rest.commit(db, ctx)
    await db.refresh(agent)
    return await _agent_response(agent, db)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    req: Request,
    namespace: str,
    name: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent (soft delete)."""
    user_id, permissions = current_user_data

    # Check permissions first to determine query scope
    has_all_permission = check_permission(permissions, "sinas.agents.delete:all")

    if has_all_permission:
        # Admin can delete all agents - don't filter by user_id
        agent = await Agent.get_by_name(db, namespace, name, user_id=None)
        set_permission_used(req, "sinas.agents.delete:all")
    else:
        # Regular user - filter by user_id
        agent = await Agent.get_by_name(db, namespace, name, user_id=user_id)
        set_permission_used(req, f"sinas.agents/{namespace}/{name}.delete:own")

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{namespace}/{name}' not found"
        )

    # Additional ownership check for non-admin users (user_id from the token is
    # a str; agent.user_id is a UUID — compare as strings)
    if not has_all_permission and str(agent.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized to delete this agent")

    # A soft delete: switched off (recorded), restorable with an update.
    ctx = rest.api_context(db, user_id)
    agent = await rest.locked(_applier, ctx, agent)
    current = _applier.spec_from_row(agent, await provider_name(db, agent.llm_provider_id))
    await rest.write(
        _applier, ctx, current.model_copy(update={"is_active": False}), existing=agent
    )
    await rest.commit(db, ctx)
    return None
