"""Webhooks API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.webhook import Webhook
from app.schemas import WebhookCreate, WebhookResponse, WebhookUpdate

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse)
async def create_webhook(
    request: Request,
    webhook_data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Create a new webhook."""
    user_id, permissions = current_user_data

    # Check create permission
    create_perm = "sinas.webhooks.create:own"
    if not check_permission(permissions, create_perm):
        set_permission_used(request, create_perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to create webhooks")
    set_permission_used(request, create_perm)

    # Check if path already exists for this user
    result = await db.execute(
        select(Webhook).where(and_(Webhook.user_id == user_id, Webhook.path == webhook_data.path))
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Webhook path '{webhook_data.path}' already exists"
        )

    # Verify function exists
    from app.models.function import Function

    function = await Function.get_by_name(
        db, webhook_data.function_namespace, webhook_data.function_name, user_id
    )
    if not function:
        raise HTTPException(
            status_code=404,
            detail=f"Function '{webhook_data.function_namespace}.{webhook_data.function_name}' not found",
        )
    # Create webhook
    webhook = Webhook(
        user_id=user_id,
        path=webhook_data.path,
        function_namespace=webhook_data.function_namespace,
        function_name=webhook_data.function_name,
        http_method=webhook_data.http_method,
        description=webhook_data.description,
        default_values=webhook_data.default_values or {},
        requires_auth=webhook_data.requires_auth,
    )

    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)

    response = WebhookResponse.model_validate(webhook)

    return response


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """List webhooks (own and group-accessible)."""
    user_id, permissions = current_user_data

    # Build query based on permissions
    if check_permission(permissions, "sinas.webhooks.read:all"):
        set_permission_used(request, "sinas.webhooks.read:all")
        query = select(Webhook)
    else:
        set_permission_used(request, "sinas.webhooks.read:own")
        query = select(Webhook).where(Webhook.user_id == user_id)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    webhooks = result.scalars().all()

    return [WebhookResponse.model_validate(webhook) for webhook in webhooks]


@router.get("/{path:path}", response_model=WebhookResponse)
async def get_webhook(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Get a specific webhook."""
    user_id, permissions = current_user_data

    webhook = await Webhook.get_by_path(db, path, user_id)

    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.webhooks.read:all"):
        set_permission_used(request, "sinas.webhooks.read:all")
    else:
        if webhook.user_id != user_id:
            set_permission_used(request, "sinas.webhooks.read:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to view this webhook")
        set_permission_used(request, "sinas.webhooks.read:own")

    response = WebhookResponse.model_validate(webhook)

    return response


@router.patch("/{path:path}", response_model=WebhookResponse)
async def update_webhook(
    request: Request,
    path: str,
    webhook_data: WebhookUpdate,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Update a webhook."""
    user_id, permissions = current_user_data

    webhook = await Webhook.get_by_path(db, path, user_id)

    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.webhooks.update:all"):
        set_permission_used(request, "sinas.webhooks.update:all")
    else:
        if webhook.user_id != user_id:
            set_permission_used(request, "sinas.webhooks.update:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to update this webhook")
        set_permission_used(request, "sinas.webhooks.update:own")

    # Update fields
    if webhook_data.function_namespace is not None or webhook_data.function_name is not None:
        # Use updated namespace or keep existing
        new_namespace = (
            webhook_data.function_namespace
            if webhook_data.function_namespace is not None
            else webhook.function_namespace
        )
        new_function_name = (
            webhook_data.function_name
            if webhook_data.function_name is not None
            else webhook.function_name
        )

        # Verify function reference can be updated (already checked webhook.update above)
        if (
            webhook_data.function_namespace is not None
            and webhook_data.function_namespace != webhook.function_namespace
        ):
            # Permission already validated with sinas.webhooks.update:own/all
            pass

        # Verify new function exists
        from app.models.function import Function

        function = await Function.get_by_name(db, new_namespace, new_function_name, user_id)
        if not function:
            raise HTTPException(
                status_code=404, detail=f"Function '{new_namespace}.{new_function_name}' not found"
            )

        webhook.function_namespace = new_namespace
        webhook.function_name = new_function_name

    if webhook_data.http_method is not None:
        webhook.http_method = webhook_data.http_method
    if webhook_data.description is not None:
        webhook.description = webhook_data.description
    if webhook_data.default_values is not None:
        webhook.default_values = webhook_data.default_values
    if webhook_data.is_active is not None:
        webhook.is_active = webhook_data.is_active
    if webhook_data.requires_auth is not None:
        webhook.requires_auth = webhook_data.requires_auth
    await db.commit()
    await db.refresh(webhook)

    response = WebhookResponse.model_validate(webhook)

    return response


@router.delete("/{path:path}")
async def delete_webhook(
    request: Request,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user_data=Depends(get_current_user_with_permissions),
):
    """Delete a webhook."""
    user_id, permissions = current_user_data

    webhook = await Webhook.get_by_path(db, path, user_id)

    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook '{path}' not found")

    # Check permissions
    if check_permission(permissions, "sinas.webhooks.delete:all"):
        set_permission_used(request, "sinas.webhooks.delete:all")
    else:
        if webhook.user_id != user_id:
            set_permission_used(request, "sinas.webhooks.delete:own", has_perm=False)
            raise HTTPException(status_code=403, detail="Not authorized to delete this webhook")
        set_permission_used(request, "sinas.webhooks.delete:own")

    await db.delete(webhook)
    await db.commit()

    return {"message": f"Webhook '{webhook.path}' deleted successfully"}
