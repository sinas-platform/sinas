"""Assistant endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.core.auth import get_current_user, get_current_user_with_permissions, require_permission, set_permission_used
from app.core.permissions import check_permission
from app.models import Assistant
from app.schemas.assistant import (
    AssistantCreate,
    AssistantUpdate,
    AssistantResponse,
)

router = APIRouter()


# Assistant endpoints

@router.post("", response_model=AssistantResponse, status_code=status.HTTP_201_CREATED)
async def create_assistant(
    request: AssistantCreate,
    user_id: str = Depends(require_permission("sinas.assistants.create:own")),
    db: AsyncSession = Depends(get_db)
):
    """Create a new assistant."""
    assistant = Assistant(
        user_id=user_id,
        group_id=request.group_id,
        name=request.name,
        description=request.description,
        system_prompt=request.system_prompt,
        enabled_webhooks=request.enabled_webhooks or [],
        enabled_mcp_tools=request.enabled_mcp_tools or [],
        webhook_parameters=request.webhook_parameters or {},
        mcp_tool_parameters=request.mcp_tool_parameters or {},
        context_namespaces=request.context_namespaces,
        ontology_namespaces=request.ontology_namespaces,
        ontology_concepts=request.ontology_concepts,
        is_active=True
    )

    db.add(assistant)
    await db.commit()
    await db.refresh(assistant)

    return AssistantResponse.model_validate(assistant)


@router.get("", response_model=List[AssistantResponse])
async def list_assistants(
    request: Request,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all assistants for the current user, or all assistants if user has read:all permission."""
    user_id, permissions = current_user_data

    # Check if user has read:all permission (e.g., admin)
    if check_permission(permissions, "sinas.assistants.read:all"):
        set_permission_used(request, "sinas.assistants.read:all", has_perm=True)
        # Return all assistants
        result = await db.execute(
            select(Assistant).where(
                Assistant.is_active == True
            ).order_by(Assistant.created_at.desc())
        )
    else:
        set_permission_used(request, "sinas.assistants.read:own", has_perm=True)
        # Return only user's own assistants
        result = await db.execute(
            select(Assistant).where(
                Assistant.user_id == user_id,
                Assistant.is_active == True
            ).order_by(Assistant.created_at.desc())
        )

    assistants = result.scalars().all()
    return [AssistantResponse.model_validate(asst) for asst in assistants]


@router.get("/{assistant_id}", response_model=AssistantResponse)
async def get_assistant(
    request: Request,
    assistant_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get an assistant."""
    set_permission_used(request, "sinas.assistants.read:own")

    result = await db.execute(
        select(Assistant).where(
            Assistant.id == assistant_id,
            Assistant.user_id == user_id
        )
    )
    assistant = result.scalar_one_or_none()

    if not assistant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant not found"
        )

    return AssistantResponse.model_validate(assistant)


@router.put("/{assistant_id}", response_model=AssistantResponse)
async def update_assistant(
    assistant_id: str,
    request: AssistantUpdate,
    user_id: str = Depends(require_permission("sinas.assistants.update:own")),
    db: AsyncSession = Depends(get_db)
):
    """Update an assistant."""
    result = await db.execute(
        select(Assistant).where(
            Assistant.id == assistant_id,
            Assistant.user_id == user_id
        )
    )
    assistant = result.scalar_one_or_none()

    if not assistant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant not found"
        )

    if request.name is not None:
        assistant.name = request.name
    if request.description is not None:
        assistant.description = request.description
    if request.system_prompt is not None:
        assistant.system_prompt = request.system_prompt
    if request.enabled_webhooks is not None:
        assistant.enabled_webhooks = request.enabled_webhooks
    if request.enabled_mcp_tools is not None:
        assistant.enabled_mcp_tools = request.enabled_mcp_tools
    if request.webhook_parameters is not None:
        assistant.webhook_parameters = request.webhook_parameters
    if request.mcp_tool_parameters is not None:
        assistant.mcp_tool_parameters = request.mcp_tool_parameters
    if request.context_namespaces is not None:
        assistant.context_namespaces = request.context_namespaces
    if request.ontology_namespaces is not None:
        assistant.ontology_namespaces = request.ontology_namespaces
    if request.ontology_concepts is not None:
        assistant.ontology_concepts = request.ontology_concepts
    if request.is_active is not None:
        assistant.is_active = request.is_active

    await db.commit()
    await db.refresh(assistant)

    return AssistantResponse.model_validate(assistant)


@router.delete("/{assistant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assistant(
    assistant_id: str,
    user_id: str = Depends(require_permission("sinas.assistants.delete:own")),
    db: AsyncSession = Depends(get_db)
):
    """Delete an assistant (soft delete)."""
    result = await db.execute(
        select(Assistant).where(
            Assistant.id == assistant_id,
            Assistant.user_id == user_id
        )
    )
    assistant = result.scalar_one_or_none()

    if not assistant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assistant not found"
        )

    assistant.is_active = False
    await db.commit()

    return None
