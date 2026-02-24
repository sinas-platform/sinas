"""Agent endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select, update
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

router = APIRouter()


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

    # Check if agent name already exists in this namespace
    result = await db.execute(
        select(Agent).where(
            and_(Agent.namespace == agent_data.namespace, Agent.name == agent_data.name)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent_data.namespace}/{agent_data.name}' already exists",
        )
    # If setting as default, unset other defaults
    if agent_data.is_default:
        await db.execute(update(Agent).values(is_default=False))

    agent = Agent(
        user_id=user_id,
        namespace=agent_data.namespace,
        name=agent_data.name,
        description=agent_data.description,
        llm_provider_id=agent_data.llm_provider_id,
        model=agent_data.model,
        temperature=agent_data.temperature or 0.7,
        max_tokens=agent_data.max_tokens,
        system_prompt=agent_data.system_prompt,
        input_schema=agent_data.input_schema or {},
        output_schema=agent_data.output_schema or {},
        initial_messages=agent_data.initial_messages,
        enabled_functions=agent_data.enabled_functions or [],
        enabled_agents=agent_data.enabled_agents or [],
        enabled_skills=[skill.model_dump() for skill in agent_data.enabled_skills]
        if agent_data.enabled_skills
        else [],
        function_parameters=agent_data.function_parameters or {},
        enabled_queries=agent_data.enabled_queries or [],
        query_parameters=agent_data.query_parameters or {},
        state_namespaces_readonly=agent_data.state_namespaces_readonly or [],
        state_namespaces_readwrite=agent_data.state_namespaces_readwrite or [],
        enabled_collections=agent_data.enabled_collections or [],
        is_active=True,
        is_default=agent_data.is_default or False,
    )

    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    response = AgentResponse.model_validate(agent)

    return response


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

    return [AgentResponse.model_validate(agent) for agent in agents]


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

    set_permission_used(req, f"sinas.agents/{namespace}/{name}.read")

    return AgentResponse.model_validate(agent)


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

    if has_all_permission:
        # Admin can update all agents - don't filter by user_id
        agent = await Agent.get_by_name(db, namespace, name, user_id=None)
        set_permission_used(req, "sinas.agents.update:all")
    else:
        # Regular user - filter by user_id
        agent = await Agent.get_by_name(db, namespace, name, user_id=user_id)
        set_permission_used(req, f"sinas.agents/{namespace}/{name}.update:own")

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{namespace}/{name}' not found"
        )

    # Additional ownership check for non-admin users
    if not has_all_permission and agent.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this agent")

    # Update fields
    if agent_data.namespace is not None:
        agent.namespace = agent_data.namespace
    if agent_data.name is not None:
        agent.name = agent_data.name
    if agent_data.description is not None:
        agent.description = agent_data.description
    if agent_data.llm_provider_id is not None:
        agent.llm_provider_id = agent_data.llm_provider_id
    if agent_data.model is not None:
        agent.model = agent_data.model
    if agent_data.temperature is not None:
        agent.temperature = agent_data.temperature
    if agent_data.max_tokens is not None:
        agent.max_tokens = agent_data.max_tokens
    if agent_data.system_prompt is not None:
        agent.system_prompt = agent_data.system_prompt
    if agent_data.input_schema is not None:
        agent.input_schema = agent_data.input_schema
    if agent_data.output_schema is not None:
        agent.output_schema = agent_data.output_schema
    if agent_data.initial_messages is not None:
        agent.initial_messages = agent_data.initial_messages
    if agent_data.enabled_functions is not None:
        agent.enabled_functions = agent_data.enabled_functions
    if agent_data.enabled_agents is not None:
        agent.enabled_agents = agent_data.enabled_agents
    if agent_data.enabled_skills is not None:
        agent.enabled_skills = [skill.model_dump() for skill in agent_data.enabled_skills]
    if agent_data.function_parameters is not None:
        agent.function_parameters = agent_data.function_parameters
    if agent_data.enabled_queries is not None:
        agent.enabled_queries = agent_data.enabled_queries
    if agent_data.query_parameters is not None:
        agent.query_parameters = agent_data.query_parameters
    if agent_data.state_namespaces_readonly is not None:
        agent.state_namespaces_readonly = agent_data.state_namespaces_readonly
    if agent_data.state_namespaces_readwrite is not None:
        agent.state_namespaces_readwrite = agent_data.state_namespaces_readwrite
    if agent_data.enabled_collections is not None:
        agent.enabled_collections = agent_data.enabled_collections
    if agent_data.is_active is not None:
        agent.is_active = agent_data.is_active
    if agent_data.is_default is not None:
        if agent_data.is_default:
            await db.execute(
                update(Agent).where(Agent.id != agent.id).values(is_default=False)
            )
        agent.is_default = agent_data.is_default
    await db.commit()
    await db.refresh(agent)

    response = AgentResponse.model_validate(agent)

    return response


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

    # Additional ownership check for non-admin users
    if not has_all_permission and agent.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this agent")

    agent.is_active = False
    await db.commit()

    return None
