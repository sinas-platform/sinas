"""Webhooks API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.agent import Agent
from app.models.function import Function
from app.models.webhook import Webhook
from app.schemas import WebhookCreate, WebhookResponse, WebhookUpdate
from app.services.package_service import detach_if_package_managed

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
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

    # Verify the target resource exists
    if webhook_data.target_type == "function":
        function = await Function.get_by_name(
            db, webhook_data.function_namespace, webhook_data.function_name, user_id
        )
        if not function:
            raise HTTPException(
                status_code=404,
                detail=f"Function '{webhook_data.function_namespace}.{webhook_data.function_name}' not found",
            )
    elif webhook_data.target_type == "pipeline":
        from app.models.pipeline import Pipeline

        pipeline = await Pipeline.get_by_name(
            db, webhook_data.pipeline_namespace, webhook_data.pipeline_name
        )
        if not pipeline:
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline '{webhook_data.pipeline_namespace}/{webhook_data.pipeline_name}' not found",
            )
        # Fail fast if the creator can't run the target (authoritative check is
        # at execution time, same rationale as agent targets below).
        _run_perm = (
            f"sinas.pipelines/{webhook_data.pipeline_namespace}/{webhook_data.pipeline_name}.run:own"
        )
        if not check_permission(permissions, _run_perm):
            set_permission_used(request, _run_perm, has_perm=False)
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to run pipeline '{webhook_data.pipeline_namespace}/{webhook_data.pipeline_name}'",
            )
    else:
        # Agents are shared by design (chat:all/read:all are default grants), so
        # the lookup is intentionally not ownership-scoped — the permission check
        # below is what authorizes the target.
        agent = await Agent.get_by_name(
            db, webhook_data.agent_namespace, webhook_data.agent_name
        )
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{webhook_data.agent_namespace}/{webhook_data.agent_name}' not found",
            )
        # Fail fast if the creator can't chat with the target: a webhook they
        # could never legitimately trigger shouldn't be creatable. This mirrors
        # POST /agents/{ns}/{name}/invoke. The authoritative check is at
        # execution time (permissions can be narrowed after creation).
        _chat_perm = (
            f"sinas.agents/{webhook_data.agent_namespace}/{webhook_data.agent_name}.chat:all"
        )
        if not check_permission(permissions, _chat_perm):
            set_permission_used(request, _chat_perm, has_perm=False)
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to chat with agent '{webhook_data.agent_namespace}/{webhook_data.agent_name}'",
            )
    # Create webhook
    webhook = Webhook(
        user_id=user_id,
        path=webhook_data.path,
        target_type=webhook_data.target_type,
        function_namespace=webhook_data.function_namespace,
        function_name=webhook_data.function_name,
        agent_namespace=webhook_data.agent_namespace if webhook_data.target_type == "agent" else None,
        agent_name=webhook_data.agent_name,
        pipeline_namespace=webhook_data.pipeline_namespace if webhook_data.target_type == "pipeline" else None,
        pipeline_name=webhook_data.pipeline_name,
        message_template=webhook_data.message_template,
        session_key_template=webhook_data.session_key_template,
        http_method=webhook_data.http_method,
        description=webhook_data.description,
        default_values=webhook_data.default_values or {},
        requires_auth=webhook_data.requires_auth,
        response_mode=webhook_data.response_mode,
        dedup=webhook_data.dedup.model_dump() if webhook_data.dedup else None,
    )

    db.add(webhook)
    await db.flush()
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

    detach_if_package_managed(webhook)

    # Update fields
    if webhook_data.target_type is not None:
        webhook.target_type = webhook_data.target_type

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
        function = await Function.get_by_name(db, new_namespace, new_function_name, user_id)
        if not function:
            raise HTTPException(
                status_code=404, detail=f"Function '{new_namespace}.{new_function_name}' not found"
            )

        webhook.function_namespace = new_namespace
        webhook.function_name = new_function_name

    if webhook_data.agent_namespace is not None or webhook_data.agent_name is not None:
        new_agent_namespace = (
            webhook_data.agent_namespace
            if webhook_data.agent_namespace is not None
            else (webhook.agent_namespace or "default")
        )
        new_agent_name = (
            webhook_data.agent_name if webhook_data.agent_name is not None else webhook.agent_name
        )

        # Verify the new agent exists; reachability is enforced by the permission
        # check below rather than an ownership filter (agents are shared by design).
        agent = await Agent.get_by_name(db, new_agent_namespace, new_agent_name)
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{new_agent_namespace}/{new_agent_name}' not found",
            )
        _chat_perm = f"sinas.agents/{new_agent_namespace}/{new_agent_name}.chat:all"
        if not check_permission(permissions, _chat_perm):
            set_permission_used(request, _chat_perm, has_perm=False)
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to chat with agent '{new_agent_namespace}/{new_agent_name}'",
            )

        webhook.agent_namespace = new_agent_namespace
        webhook.agent_name = new_agent_name

    if webhook_data.pipeline_namespace is not None or webhook_data.pipeline_name is not None:
        from app.models.pipeline import Pipeline

        new_pipeline_namespace = (
            webhook_data.pipeline_namespace
            if webhook_data.pipeline_namespace is not None
            else (webhook.pipeline_namespace or "default")
        )
        new_pipeline_name = (
            webhook_data.pipeline_name
            if webhook_data.pipeline_name is not None
            else webhook.pipeline_name
        )

        pipeline = await Pipeline.get_by_name(db, new_pipeline_namespace, new_pipeline_name)
        if not pipeline:
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline '{new_pipeline_namespace}/{new_pipeline_name}' not found",
            )
        _run_perm = f"sinas.pipelines/{new_pipeline_namespace}/{new_pipeline_name}.run:own"
        if not check_permission(permissions, _run_perm):
            set_permission_used(request, _run_perm, has_perm=False)
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to run pipeline '{new_pipeline_namespace}/{new_pipeline_name}'",
            )

        webhook.pipeline_namespace = new_pipeline_namespace
        webhook.pipeline_name = new_pipeline_name

    # `is not None` can't express "clear this field" — for optional fields that
    # a user can legitimately unset, distinguish "absent from the request" from
    # "explicitly sent as null" via the fields actually provided. Without this,
    # clearing a session key or turning dedup off was a silent no-op that still
    # reported success.
    provided = webhook_data.model_fields_set

    if webhook_data.message_template is not None:
        webhook.message_template = webhook_data.message_template
    if "session_key_template" in provided:
        webhook.session_key_template = webhook_data.session_key_template or None

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
    if webhook_data.response_mode is not None:
        webhook.response_mode = webhook_data.response_mode
    if "dedup" in provided:
        webhook.dedup = webhook_data.dedup.model_dump() if webhook_data.dedup else None

    # Validate resulting target configuration
    if webhook.target_type == "function":
        if not webhook.function_name:
            raise HTTPException(
                status_code=400, detail="function_name is required for function-target webhooks"
            )
        if webhook.response_mode not in ("sync", "async", "raw"):
            raise HTTPException(status_code=400, detail="Invalid response_mode")
    elif webhook.target_type == "pipeline":
        if not webhook.pipeline_name:
            raise HTTPException(
                status_code=400, detail="pipeline_name is required for pipeline-target webhooks"
            )
        if webhook.response_mode == "raw":
            raise HTTPException(
                status_code=400,
                detail="response_mode 'raw' is only supported for function-target webhooks",
            )
    else:
        if not webhook.agent_name or not webhook.message_template:
            raise HTTPException(
                status_code=400,
                detail="agent_name and message_template are required for agent-target webhooks",
            )
        if webhook.response_mode == "raw":
            raise HTTPException(
                status_code=400,
                detail="response_mode 'raw' is only supported for function-target webhooks",
            )

    await db.flush()
    await db.refresh(webhook)

    response = WebhookResponse.model_validate(webhook)

    return response


@router.delete("/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
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
    await db.flush()

    return None
