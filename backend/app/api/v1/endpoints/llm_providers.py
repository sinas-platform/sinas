"""LLM Provider endpoints for managing LLM configurations."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid

from app.core.database import get_db
from app.core.auth import require_permission
from app.core.encryption import EncryptionService
from app.models import LLMProvider
from app.schemas.llm_provider import (
    LLMProviderCreate,
    LLMProviderUpdate,
    LLMProviderResponse,
)

router = APIRouter()


@router.post("", response_model=LLMProviderResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    request: LLMProviderCreate,
    user_id: str = Depends(require_permission("sinas.llm_providers.post:all")),
    db: AsyncSession = Depends(get_db)
):
    """Create a new LLM provider configuration. Admin only."""
    # Check if provider with same name already exists
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.name == request.name)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider with name '{request.name}' already exists"
        )

    # If this is set as default, unset other defaults
    if request.is_default:
        await db.execute(
            LLMProvider.__table__.update().values(is_default=False)
        )

    # Encrypt API key if provided
    encrypted_api_key = None
    if request.api_key:
        encryption_service = EncryptionService()
        encrypted_api_key = encryption_service.encrypt(request.api_key)

    provider = LLMProvider(
        name=request.name,
        provider_type=request.provider_type,
        api_key=encrypted_api_key,
        api_endpoint=request.api_endpoint,
        default_model=request.default_model,
        config=request.config or {},
        is_default=request.is_default or False,
        is_active=True
    )

    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    return LLMProviderResponse.model_validate(provider)


@router.get("", response_model=List[LLMProviderResponse])
async def list_llm_providers(
    user_id: str = Depends(require_permission("sinas.llm_providers.get:all")),
    db: AsyncSession = Depends(get_db)
):
    """List all LLM providers. Admin only."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.is_active == True).order_by(LLMProvider.created_at.desc())
    )
    providers = result.scalars().all()
    return [LLMProviderResponse.model_validate(p) for p in providers]


@router.get("/{name}", response_model=LLMProviderResponse)
async def get_llm_provider(
    name: str,
    user_id: str = Depends(require_permission("sinas.llm_providers.get:all")),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific LLM provider by name. Admin only."""
    provider = await LLMProvider.get_by_name(db, name)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{name}' not found"
        )
    return LLMProviderResponse.model_validate(provider)


@router.patch("/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider(
    provider_id: uuid.UUID,
    request: LLMProviderUpdate,
    user_id: str = Depends(require_permission("sinas.llm_providers.put:all")),
    db: AsyncSession = Depends(get_db)
):
    """Update an LLM provider. Admin only."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found"
        )

    # If setting as default, unset other defaults
    if request.is_default:
        await db.execute(
            LLMProvider.__table__.update().where(LLMProvider.id != provider.id).values(is_default=False)
        )

    # Update fields
    if request.name is not None:
        # Check name uniqueness
        name_check = await db.execute(
            select(LLMProvider).where(LLMProvider.name == request.name, LLMProvider.id != provider.id)
        )
        if name_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider with name '{request.name}' already exists"
            )
        provider.name = request.name

    if request.provider_type is not None:
        provider.provider_type = request.provider_type

    if request.api_key is not None:
        encryption_service = EncryptionService()
        provider.api_key = encryption_service.encrypt(request.api_key)

    if request.api_endpoint is not None:
        provider.api_endpoint = request.api_endpoint

    if request.default_model is not None:
        provider.default_model = request.default_model

    if request.config is not None:
        provider.config = request.config

    if request.is_default is not None:
        provider.is_default = request.is_default

    if request.is_active is not None:
        provider.is_active = request.is_active

    await db.commit()
    await db.refresh(provider)

    return LLMProviderResponse.model_validate(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(
    provider_id: uuid.UUID,
    user_id: str = Depends(require_permission("sinas.llm_providers.delete:all")),
    db: AsyncSession = Depends(get_db)
):
    """Soft delete an LLM provider. Admin only."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_id}' not found"
        )

    provider.is_active = False
    await db.commit()
