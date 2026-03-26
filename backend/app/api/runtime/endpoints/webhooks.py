"""Runtime webhook endpoints - execute functions via HTTP."""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import set_permission_used, verify_jwt_or_api_key
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.webhook import Webhook
from app.services.dedup_service import check_and_mark, store_result
from app.services.queue_service import queue_service


router = APIRouter()



@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def execute_webhook(
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Execute webhook by triggering associated function."""
    # Look up webhook configuration
    result = await db.execute(
        select(Webhook).where(
            and_(
                Webhook.path == path,
                Webhook.http_method == request.method,
                Webhook.is_active == True,
            )
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=404,
            detail=f"No active webhook found for path '{path}' and method '{request.method}'",
        )

    # Authenticate if required
    user_id: Optional[str] = None
    if webhook.requires_auth:
        auth_header = request.headers.get("authorization")
        api_key_header = request.headers.get("x-api-key")

        if not auth_header and not api_key_header:
            raise HTTPException(status_code=401, detail="Authorization required")

        try:
            # Build credentials in the format verify_jwt_or_api_key expects
            from fastapi.security import HTTPAuthorizationCredentials

            credentials = None
            if auth_header and auth_header.lower().startswith("bearer "):
                credentials = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=auth_header[7:]
                )

            user_id, email, permissions = await verify_jwt_or_api_key(
                credentials=credentials,
                x_api_key=api_key_header,
                db=db,
            )

            # Check function execute permission
            function_perm = f"sinas.functions/{webhook.function_namespace}/{webhook.function_name}.execute:own"
            function_perm_all = f"sinas.functions/{webhook.function_namespace}/{webhook.function_name}.execute:all"

            has_permission = check_permission(permissions, function_perm_all) or (
                check_permission(permissions, function_perm) and str(webhook.user_id) == user_id
            )

            if not has_permission:
                set_permission_used(request, function_perm, has_perm=False)
                raise HTTPException(status_code=403, detail=f"Not authorized to execute webhook '{path}'")

            set_permission_used(
                request,
                function_perm_all if check_permission(permissions, function_perm_all) else function_perm,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")
    else:
        # Use webhook owner's user_id for unauthenticated webhooks
        user_id = str(webhook.user_id)
        set_permission_used(request, f"webhook.public:{webhook.path}")

    try:
        # Extract the request body as function input
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                input_data = await request.json()
            except Exception:
                input_data = {}
        elif request.method == "GET":
            input_data = dict(request.query_params)
        else:
            input_data = {}

        # Merge default values (body overrides defaults)
        if webhook.default_values:
            final_input = {**webhook.default_values, **(input_data if isinstance(input_data, dict) else {"input": input_data})}
        else:
            final_input = input_data

        # Deduplication check
        if webhook.dedup:
            req_headers = dict(request.headers)
            is_dup, cached = await check_and_mark(
                webhook_id=str(webhook.id),
                body=final_input if isinstance(final_input, dict) else {},
                headers=req_headers,
                dedup_config=webhook.dedup,
            )
            if is_dup:
                if cached:
                    return JSONResponse(json.loads(cached), status_code=200)
                return JSONResponse({"deduplicated": True}, status_code=200)

        execution_id = str(uuid.uuid4())
        chat_id = request.headers.get("x-chat-id")

        # Async mode: return immediately
        if webhook.response_mode == "async":
            await queue_service.enqueue_function(
                function_namespace=webhook.function_namespace,
                function_name=webhook.function_name,
                input_data=final_input,
                execution_id=execution_id,
                trigger_type=TriggerType.WEBHOOK.value,
                trigger_id=str(webhook.id),
                user_id=user_id,
                chat_id=chat_id,
            )
            return JSONResponse({"execution_id": execution_id}, status_code=202)

        # Sync mode (default): wait for result
        result = await queue_service.enqueue_and_wait(
            function_namespace=webhook.function_namespace,
            function_name=webhook.function_name,
            input_data=final_input,
            execution_id=execution_id,
            trigger_type=TriggerType.WEBHOOK.value,
            trigger_id=str(webhook.id),
            user_id=user_id,
            chat_id=chat_id,
        )

        response = {"success": True, "execution_id": execution_id, "result": result}

        # Cache result for dedup
        if webhook.dedup:
            try:
                req_headers = dict(request.headers)
                await store_result(
                    webhook_id=str(webhook.id),
                    body=final_input if isinstance(final_input, dict) else {},
                    headers=req_headers,
                    dedup_config=webhook.dedup,
                    result=json.dumps(response),
                )
            except Exception:
                pass  # Non-critical

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Function execution failed: {str(e)}")
