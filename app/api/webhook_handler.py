from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Any
import uuid

from app.core.database import get_db
from app.core.auth import get_subtenant_context
from app.models.webhook import Webhook
from app.models.execution import TriggerType
from app.services.execution_engine import executor

router = APIRouter(prefix="/h", tags=["webhook-handler"])


async def extract_request_data(request: Request) -> Dict[str, Any]:
    """Extract all request data (body, headers, query params) into a structured format."""
    # Get request body
    body = {}
    content_type = request.headers.get("content-type", "")
    
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
    elif "application/x-www-form-urlencoded" in content_type:
        try:
            form_data = await request.form()
            body = dict(form_data)
        except Exception:
            body = {}
    else:
        # Try to get raw body as text
        try:
            raw_body = await request.body()
            body = {"raw": raw_body.decode("utf-8")} if raw_body else {}
        except Exception:
            body = {}
    
    # Extract headers (exclude some internal ones)
    headers = {
        k: v for k, v in request.headers.items() 
        if not k.lower().startswith(('host', 'user-agent', 'accept-encoding'))
    }
    
    # Extract query parameters
    query = dict(request.query_params)
    
    # Extract path parameters
    path_params = dict(request.path_params)
    
    return {
        "body": body,
        "headers": headers,
        "query": query,
        "path_params": path_params,
        "method": request.method,
        "url": str(request.url),
        "path": request.url.path
    }


@router.api_route(
    "/{path:path}", 
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    operation_id="handle_webhook_request"
)
async def handle_webhook(
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    """Handle incoming webhook requests by executing the associated function."""
    
    # Look up webhook configuration
    result = await db.execute(
        select(Webhook).where(
            and_(
                Webhook.path == path,
                Webhook.http_method == request.method,
                Webhook.is_active == True
            )
        )
    )
    webhook = result.scalar_one_or_none()
    print("WEBHOOK FOUND:", webhook)
    
    if not webhook:
        raise HTTPException(
            status_code=404, 
            detail=f"No active webhook found for path '{path}' and method '{request.method}'"
        )
    
    # TODO: Check authentication if webhook.requires_auth is True
    
    try:
        # Extract request data
        request_data = await extract_request_data(request)
        
        # Merge with default values if provided
        if webhook.default_values:
            # Default values are applied at the top level
            final_input = {**webhook.default_values, **request_data}
        else:
            final_input = request_data
        
        # Generate execution ID
        execution_id = str(uuid.uuid4())
        
        # Execute the function
        result = await executor.execute_function(
            function_name=webhook.function_name,
            input_data=final_input,
            execution_id=execution_id,
            trigger_type=TriggerType.WEBHOOK.value,
            trigger_id=str(webhook.id),
            subtenant_id=subtenant_id
        )
        
        return {
            "success": True,
            "execution_id": execution_id,
            "result": result
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Function execution failed: {str(e)}"
        )