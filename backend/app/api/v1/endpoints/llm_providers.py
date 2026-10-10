"""LLM Provider endpoints for managing LLM configurations.

Writes go through LLMProviderApplier, the path config apply uses too: the
same ownership and change history (the API key redacted) on every channel.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.database import get_db
from app.models import LLMProvider
from app.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderResponse,
    LLMProviderUpdate,
)

from app.services.resources import rest
from app.services.resources.llm_providers import LLMProviderApplier

router = APIRouter()

_applier = LLMProviderApplier()


@router.post("", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    request: LLMProviderCreate,
    user_id: str = Depends(require_permission("sinas.llm_providers.create:all")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new LLM provider configuration. Admin only."""
    ctx = rest.api_context(db, user_id)
    data = request.model_dump()
    data["is_default"] = bool(data.get("is_default"))
    try:
        result = await rest.write(_applier, ctx, rest.parse_spec(_applier, data), must_create=True)
    except HTTPException as e:
        if e.status_code == 400 and "already exists" in str(e.detail):
            raise HTTPException(status_code=400, detail=f"Provider with name '{request.name}' already exists")
        raise
    await rest.commit(db, ctx)
    await db.refresh(result.obj)
    return LLMProviderResponse.model_validate(result.obj)


@router.get("", response_model=list[LLMProviderResponse])
async def list_llm_providers(
    user_id: str = Depends(require_permission("sinas.llm_providers.read:all")),
    db: AsyncSession = Depends(get_db),
):
    """List all LLM providers (including inactive). Admin only."""
    result = await db.execute(
        select(LLMProvider).order_by(LLMProvider.created_at.desc())
    )
    providers = result.scalars().all()
    return [LLMProviderResponse.model_validate(p) for p in providers]


@router.get("/{name}", response_model=LLMProviderResponse)
async def get_llm_provider(
    name: str,
    user_id: str = Depends(require_permission("sinas.llm_providers.read:all")),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific LLM provider by name. Admin only."""
    provider = await LLMProvider.get_by_name(db, name)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Provider '{name}' not found"
        )
    return LLMProviderResponse.model_validate(provider)


@router.patch("/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(
    provider_id: uuid.UUID,
    request: LLMProviderUpdate,
    user_id: str = Depends(require_permission("sinas.llm_providers.update:all")),
    db: AsyncSession = Depends(get_db),
):
    """Update an LLM provider. Admin only."""
    result = await db.execute(select(LLMProvider).where(LLMProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Provider '{provider_id}' not found"
        )

    ctx = rest.api_context(db, user_id)
    provider = await rest.locked(_applier, ctx, provider)
    # As before: a field left out or sent as null stays as it is; config is
    # replaced as a whole.
    patch = {f: v for f, v in request.model_dump(exclude_unset=True).items() if v is not None}
    try:
        await rest.write(_applier, ctx, rest.patch_spec(_applier, provider, patch), existing=provider)
    except HTTPException as e:
        if e.status_code == 400 and "already exists" in str(e.detail):
            raise HTTPException(status_code=400, detail=f"Provider with name '{request.name}' already exists")
        raise
    await rest.commit(db, ctx)
    await db.refresh(provider)
    return LLMProviderResponse.model_validate(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(
    provider_id: uuid.UUID,
    user_id: str = Depends(require_permission("sinas.llm_providers.delete:all")),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete an LLM provider. Admin only."""
    result = await db.execute(select(LLMProvider).where(LLMProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Provider '{provider_id}' not found"
        )

    # A soft delete: agents point at it by id. Recorded; restorable by an
    # update with is_active: true.
    ctx = rest.api_context(db, user_id)
    provider = await rest.locked(_applier, ctx, provider)
    current = _applier.spec_from_row(provider)
    await rest.write(_applier, ctx, current.model_copy(update={"is_active": False}), existing=provider)
    await rest.commit(db, ctx)
