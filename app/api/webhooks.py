from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
import uuid
import json

from app.core.database import get_db
from app.core.auth import get_subtenant_context
from app.models.webhook import Webhook
from app.api.schemas import WebhookCreate, WebhookUpdate, WebhookResponse
from app.services.execution_engine import executor

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse)
async def create_webhook(
    webhook_data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    # Check if path already exists
    existing = await db.execute(
        select(Webhook).where(Webhook.path == webhook_data.path)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Webhook path already exists")
    
    # Create webhook with subtenant_id for tracking who created it
    webhook = Webhook(
        subtenant_id=subtenant_id,  # Track which subtenant created it, but can be shared
        path=webhook_data.path,
        function_name=webhook_data.function_name,
        http_method=webhook_data.http_method,
        description=webhook_data.description,
        default_values=webhook_data.default_values,
        requires_auth=webhook_data.requires_auth
    )
    
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    
    return webhook


@router.get("", response_model=List[WebhookResponse])
async def list_webhooks(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
):
    query = select(Webhook)
    
    if active_only:
        query = query.where(Webhook.is_active == True)
    
    result = await db.execute(query.order_by(Webhook.path))
    webhooks = result.scalars().all()
    
    return webhooks


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: str, db: AsyncSession = Depends(get_db)):
    try:
        webhook_uuid = uuid.UUID(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook ID format")
    
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_uuid)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    return webhook


@router.put("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: str,
    webhook_data: WebhookUpdate,
    db: AsyncSession = Depends(get_db)
):
    try:
        webhook_uuid = uuid.UUID(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook ID format")
    
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_uuid)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    # Update webhook fields
    update_data = webhook_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(webhook, field, value)
    
    await db.commit()
    await db.refresh(webhook)
    
    return webhook


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str, db: AsyncSession = Depends(get_db)):
    try:
        webhook_uuid = uuid.UUID(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook ID format")
    
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_uuid)
    )
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    
    # Soft delete by setting is_active to False
    webhook.is_active = False
    
    await db.commit()
    
    return {"message": "Webhook deleted successfully"}