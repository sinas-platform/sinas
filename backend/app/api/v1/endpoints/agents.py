"""Agent endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.core.auth import get_current_user, get_current_user_with_permissions, require_permission, set_permission_used
from app.core.permissions import check_permission
from app.models import Agent
from app.schemas.agent import (
    AgentCreate,
    AgentUpdate,
    AgentResponse,
)

router = APIRouter()


# Agent endpoints

@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    req: Request,
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data = Depends(get_current_user_with_permissions)
):
    """Create a new agent."""
    user_id, permissions = current_user_data

    # Check namespace permission
    namespace_perm = f"sinas.agents.{agent_data.namespace}.post:own"
    if not check_permission(permissions, namespace_perm):
        set_permission_used(req, namespace_perm, has_perm=False)
        raise HTTPException(status_code=403, detail=f"Not authorized to create agents in namespace '{agent_data.namespace}'")
    set_permission_used(req, namespace_perm)

    # Check if agent name already exists in this namespace
    from sqlalchemy import and_
    result = await db.execute(
        select(Agent).where(
            and_(
                Agent.namespace == agent_data.namespace,
                Agent.name == agent_data.name
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Agent '{agent_data.namespace}/{agent_data.name}' already exists")

    agent = Agent(
        user_id=user_id,
        group_id=agent_data.group_id,
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
        enabled_mcp_tools=agent_data.enabled_mcp_tools or [],
        enabled_agents=agent_data.enabled_agents or [],
        function_parameters=agent_data.function_parameters or {},
        mcp_tool_parameters=agent_data.mcp_tool_parameters or {},
        state_namespaces_readonly=agent_data.state_namespaces_readonly or [],
        state_namespaces_readwrite=agent_data.state_namespaces_readwrite or [],
        is_active=True
    )

    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return AgentResponse.model_validate(agent)


@router.get("", response_model=List[AgentResponse])
async def list_agents(
    req: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all agents accessible by the current user."""
    user_id, permissions = current_user_data

    # Check if user has get:all permission (e.g., admin)
    if check_permission(permissions, "sinas.agents.*.get:all"):
        set_permission_used(req, "sinas.agents.*.get:all", has_perm=True)
        # Return all agents
        result = await db.execute(
            select(Agent).where(
                Agent.is_active == True
            ).order_by(Agent.created_at.desc())
        )
    else:
        set_permission_used(req, "sinas.agents.*.get:own", has_perm=True)
        # Return only user's own agents
        result = await db.execute(
            select(Agent).where(
                Agent.user_id == user_id,
                Agent.is_active == True
            ).order_by(Agent.created_at.desc())
        )

    agents = result.scalars().all()
    return [AgentResponse.model_validate(agent) for agent in agents]


@router.get("/{namespace}/{name}", response_model=AgentResponse)
async def get_agent(
    req: Request,
    namespace: str,
    name: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific agent by namespace and name."""
    user_id, permissions = current_user_data

    agent = await Agent.get_by_name(db, namespace, name, user_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{namespace}/{name}' not found"
        )

    # Check permissions
    if check_permission(permissions, f"sinas.agents.{namespace}.get:all"):
        set_permission_used(req, f"sinas.agents.{namespace}.get:all")
    else:
        if agent.user_id != user_id:
            set_permission_used(req, f"sinas.agents.{namespace}.get:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this agent")
        set_permission_used(req, f"sinas.agents.{namespace}.get:own")

    return AgentResponse.model_validate(agent)


@router.put("/{namespace}/{name}", response_model=AgentResponse)
async def update_agent(
    req: Request,
    namespace: str,
    name: str,
    agent_data: AgentUpdate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Update an agent."""
    user_id, permissions = current_user_data

    agent = await Agent.get_by_name(db, namespace, name, user_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{namespace}/{name}' not found"
        )

    # Check permissions
    if check_permission(permissions, f"sinas.agents.{namespace}.put:all"):
        set_permission_used(req, f"sinas.agents.{namespace}.put:all")
    else:
        if agent.user_id != user_id:
            set_permission_used(req, f"sinas.agents.{namespace}.put:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update this agent")
        set_permission_used(req, f"sinas.agents.{namespace}.put:own")

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
    if agent_data.enabled_functions is not None:
        agent.enabled_functions = agent_data.enabled_functions
    if agent_data.enabled_mcp_tools is not None:
        agent.enabled_mcp_tools = agent_data.enabled_mcp_tools
    if agent_data.enabled_agents is not None:
        agent.enabled_agents = agent_data.enabled_agents
    if agent_data.function_parameters is not None:
        agent.function_parameters = agent_data.function_parameters
    if agent_data.mcp_tool_parameters is not None:
        agent.mcp_tool_parameters = agent_data.mcp_tool_parameters
    if agent_data.state_namespaces_readonly is not None:
        agent.state_namespaces_readonly = agent_data.state_namespaces_readonly
    if agent_data.state_namespaces_readwrite is not None:
        agent.state_namespaces_readwrite = agent_data.state_namespaces_readwrite
    if agent_data.is_active is not None:
        agent.is_active = agent_data.is_active

    await db.commit()
    await db.refresh(agent)

    return AgentResponse.model_validate(agent)


@router.delete("/{namespace}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    req: Request,
    namespace: str,
    name: str,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete an agent (soft delete)."""
    user_id, permissions = current_user_data

    agent = await Agent.get_by_name(db, namespace, name, user_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{namespace}/{name}' not found"
        )

    # Check permissions
    if check_permission(permissions, f"sinas.agents.{namespace}.delete:all"):
        set_permission_used(req, f"sinas.agents.{namespace}.delete:all")
    else:
        if agent.user_id != user_id:
            set_permission_used(req, f"sinas.agents.{namespace}.delete:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to delete this agent")
        set_permission_used(req, f"sinas.agents.{namespace}.delete:own")

    agent.is_active = False
    await db.commit()

    return None
