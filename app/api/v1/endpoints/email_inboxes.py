from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.core.database import get_db
from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.permissions import check_permission
from app.models.email import EmailInbox, EmailInboxRule
from app.models.webhook import Webhook
from app.schemas.email import (
    EmailInboxCreate,
    EmailInboxUpdate,
    EmailInboxResponse,
    EmailInboxRuleCreate,
    EmailInboxRuleUpdate,
    EmailInboxRuleResponse
)

router = APIRouter(prefix="/email-inboxes", tags=["email-inboxes"])


# Email Inbox Endpoints

@router.post("/", response_model=EmailInboxResponse, status_code=status.HTTP_201_CREATED)
async def create_inbox(
    request: Request,
    inbox: EmailInboxCreate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Create a new email inbox"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.create:own"):
        set_permission_used(request, "sinas.email.inboxes.create:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create email inboxes")

    set_permission_used(request, "sinas.email.inboxes.create:own", has_perm=True)

    # Check if email address already exists
    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.email_address == inbox.email_address)
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email inbox with address '{inbox.email_address}' already exists"
        )

    # Verify webhook exists if provided
    if inbox.webhook_id:
        result = await db.execute(
            select(Webhook).filter(Webhook.id == inbox.webhook_id)
        )
        webhook = result.scalar_one_or_none()
        if not webhook:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook with id {inbox.webhook_id} not found"
            )

    db_inbox = EmailInbox(**inbox.model_dump())
    db.add(db_inbox)
    await db.commit()
    await db.refresh(db_inbox)

    return db_inbox


@router.get("/", response_model=List[EmailInboxResponse])
async def list_inboxes(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all email inboxes"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.read:own"):
        set_permission_used(request, "sinas.email.inboxes.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email inboxes")

    set_permission_used(request, "sinas.email.inboxes.read:own", has_perm=True)

    result = await db.execute(
        select(EmailInbox).offset(skip).limit(limit)
    )
    inboxes = result.scalars().all()

    return inboxes


@router.get("/{inbox_id}", response_model=EmailInboxResponse)
async def get_inbox(
    request: Request,
    inbox_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific email inbox"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.read:own"):
        set_permission_used(request, "sinas.email.inboxes.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email inboxes")

    set_permission_used(request, "sinas.email.inboxes.read:own", has_perm=True)

    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.id == inbox_id)
    )
    inbox = result.scalar_one_or_none()

    if not inbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox with id {inbox_id} not found"
        )

    return inbox


@router.patch("/{inbox_id}", response_model=EmailInboxResponse)
async def update_inbox(
    request: Request,
    inbox_id: int,
    inbox_update: EmailInboxUpdate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Update an email inbox"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.update:own"):
        set_permission_used(request, "sinas.email.inboxes.update:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update email inboxes")

    set_permission_used(request, "sinas.email.inboxes.update:own", has_perm=True)

    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.id == inbox_id)
    )
    inbox = result.scalar_one_or_none()

    if not inbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox with id {inbox_id} not found"
        )

    update_data = inbox_update.model_dump(exclude_unset=True)

    # Verify webhook exists if updating webhook_id
    if 'webhook_id' in update_data and update_data['webhook_id']:
        result = await db.execute(
            select(Webhook).filter(Webhook.id == update_data['webhook_id'])
        )
        webhook = result.scalar_one_or_none()
        if not webhook:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook with id {update_data['webhook_id']} not found"
            )

    if update_data:
        for field, value in update_data.items():
            setattr(inbox, field, value)

        await db.commit()
        await db.refresh(inbox)

    return inbox


@router.delete("/{inbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox(
    request: Request,
    inbox_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete an email inbox"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.delete:own"):
        set_permission_used(request, "sinas.email.inboxes.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete email inboxes")

    set_permission_used(request, "sinas.email.inboxes.delete:own", has_perm=True)

    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.id == inbox_id)
    )
    inbox = result.scalar_one_or_none()

    if not inbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox with id {inbox_id} not found"
        )

    await db.delete(inbox)
    await db.commit()

    return None


# Email Inbox Rule Endpoints

@router.post("/{inbox_id}/rules", response_model=EmailInboxRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_inbox_rule(
    request: Request,
    inbox_id: int,
    rule: EmailInboxRuleCreate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Create a new email inbox rule"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.rules.create:own"):
        set_permission_used(request, "sinas.email.inboxes.rules.create:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create email inbox rules")

    set_permission_used(request, "sinas.email.inboxes.rules.create:own", has_perm=True)

    # Verify inbox exists
    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.id == inbox_id)
    )
    inbox = result.scalar_one_or_none()

    if not inbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox with id {inbox_id} not found"
        )

    # Verify webhook exists if provided
    if rule.webhook_id:
        result = await db.execute(
            select(Webhook).filter(Webhook.id == rule.webhook_id)
        )
        webhook = result.scalar_one_or_none()
        if not webhook:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook with id {rule.webhook_id} not found"
            )

    # Ensure inbox_id matches
    rule_data = rule.model_dump()
    rule_data['inbox_id'] = inbox_id

    db_rule = EmailInboxRule(**rule_data)
    db.add(db_rule)
    await db.commit()
    await db.refresh(db_rule)

    return db_rule


@router.get("/{inbox_id}/rules", response_model=List[EmailInboxRuleResponse])
async def list_inbox_rules(
    request: Request,
    inbox_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """List all rules for an email inbox"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.rules.read:own"):
        set_permission_used(request, "sinas.email.inboxes.rules.read:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read email inbox rules")

    set_permission_used(request, "sinas.email.inboxes.rules.read:own", has_perm=True)

    # Verify inbox exists
    result = await db.execute(
        select(EmailInbox).filter(EmailInbox.id == inbox_id)
    )
    inbox = result.scalar_one_or_none()

    if not inbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox with id {inbox_id} not found"
        )

    result = await db.execute(
        select(EmailInboxRule).filter(EmailInboxRule.inbox_id == inbox_id).order_by(EmailInboxRule.priority.desc())
    )
    rules = result.scalars().all()

    return rules


@router.patch("/rules/{rule_id}", response_model=EmailInboxRuleResponse)
async def update_inbox_rule(
    request: Request,
    rule_id: int,
    rule_update: EmailInboxRuleUpdate,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Update an email inbox rule"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.rules.update:own"):
        set_permission_used(request, "sinas.email.inboxes.rules.update:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update email inbox rules")

    set_permission_used(request, "sinas.email.inboxes.rules.update:own", has_perm=True)

    result = await db.execute(
        select(EmailInboxRule).filter(EmailInboxRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox rule with id {rule_id} not found"
        )

    update_data = rule_update.model_dump(exclude_unset=True)

    # Verify webhook exists if updating webhook_id
    if 'webhook_id' in update_data and update_data['webhook_id']:
        result = await db.execute(
            select(Webhook).filter(Webhook.id == update_data['webhook_id'])
        )
        webhook = result.scalar_one_or_none()
        if not webhook:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Webhook with id {update_data['webhook_id']} not found"
            )

    if update_data:
        for field, value in update_data.items():
            setattr(rule, field, value)

        await db.commit()
        await db.refresh(rule)

    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inbox_rule(
    request: Request,
    rule_id: int,
    current_user_data: tuple = Depends(get_current_user_with_permissions),
    db: AsyncSession = Depends(get_db)
):
    """Delete an email inbox rule"""
    user_id, permissions = current_user_data

    if not check_permission(permissions, "sinas.email.inboxes.rules.delete:own"):
        set_permission_used(request, "sinas.email.inboxes.rules.delete:own", has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete email inbox rules")

    set_permission_used(request, "sinas.email.inboxes.rules.delete:own", has_perm=True)

    result = await db.execute(
        select(EmailInboxRule).filter(EmailInboxRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email inbox rule with id {rule_id} not found"
        )

    await db.delete(rule)
    await db.commit()

    return None
