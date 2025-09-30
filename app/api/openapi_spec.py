from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Any, List
import json

from app.core.database import get_db
from app.core.auth import get_subtenant_context
from app.models.webhook import Webhook
from app.models.function import Function

router = APIRouter(prefix="/openapi", tags=["openapi"])


def json_schema_to_openapi_schema(json_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert JSON Schema to OpenAPI 3.0 schema format."""
    # For most cases, JSON Schema and OpenAPI schema are compatible
    # This function can be extended if specific conversions are needed
    return json_schema


def generate_webhook_openapi_spec(webhooks: List[Webhook], functions: Dict[str, Function]) -> Dict[str, Any]:
    """Generate OpenAPI 3.0 spec for webhook handlers."""
    
    paths = {}
    
    for webhook in webhooks:
        path = f"/api/v1/h/{webhook.path}"
        
        # Get the function definition
        function = functions.get(webhook.function_name)
        if not function:
            continue
        
        # Generate operation info
        operation = {
            "summary": function.description or f"Execute {function.name} function",
            "description": f"Webhook handler for {function.name} function",
            "operationId": f"webhook_{webhook.function_name}_{webhook.http_method.value.lower()}",
            "tags": function.tags or ["webhooks"],
        }
        
        # Add request body schema if function expects input
        if function.input_schema:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": json_schema_to_openapi_schema(function.input_schema)
                    }
                }
            }
        
        # Add responses
        operation["responses"] = {
            "200": {
                "description": "Function executed successfully",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "execution_id": {"type": "string", "format": "uuid"},
                                "result": json_schema_to_openapi_schema(function.output_schema) if function.output_schema else {"type": "object"}
                            },
                            "required": ["success", "execution_id", "result"]
                        }
                    }
                }
            },
            "404": {
                "description": "Webhook not found",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "detail": {"type": "string"}
                            }
                        }
                    }
                }
            },
            "500": {
                "description": "Function execution failed",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "detail": {"type": "string"}
                            }
                        }
                    }
                }
            }
        }
        
        # Add authentication if required
        if webhook.requires_auth:
            operation["security"] = [{"bearerAuth": []}]
        
        # Initialize path if not exists
        if path not in paths:
            paths[path] = {}
        
        # Add operation for this HTTP method
        paths[path][webhook.http_method.value.lower()] = operation
    
    # Build the complete OpenAPI spec
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Maestro Webhook Handlers",
            "description": "Auto-generated OpenAPI specification for all webhook handlers",
            "version": "1.0.0"
        },
        "servers": [
            {
                "url": "http://localhost:8800",
                "description": "Local development server"
            }
        ],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT"
                }
            }
        }
    }
    
    return spec


@router.get("/webhooks")
async def get_webhook_openapi_spec(
    db: AsyncSession = Depends(get_db)
):
    """Generate OpenAPI 3.0 specification for all webhook handlers."""
    
    # Get all active webhooks (webhooks are shared, not per subtenant)
    webhook_result = await db.execute(
        select(Webhook).where(Webhook.is_active == True)
    )
    webhooks = webhook_result.scalars().all()
    
    if not webhooks:
        return {
            "openapi": "3.0.0",
            "info": {
                "title": "Maestro Webhook Handlers",
                "description": "No webhook handlers found",
                "version": "1.0.0"
            },
            "paths": {}
        }
    
    # Get all functions referenced by webhooks (group by subtenant since functions are per subtenant)
    function_lookups = {(webhook.subtenant_id, webhook.function_name) for webhook in webhooks}
    
    functions = {}
    for webhook_subtenant_id, function_name in function_lookups:
        function_result = await db.execute(
            select(Function).where(
                and_(
                    Function.subtenant_id == webhook_subtenant_id,
                    Function.name == function_name,
                    Function.is_active == True
                )
            )
        )
        function = function_result.scalar_one_or_none()
        if function:
            functions[function_name] = function
    
    # Generate OpenAPI spec
    spec = generate_webhook_openapi_spec(webhooks, functions)
    
    return spec


@router.get("/webhooks.json")
async def get_webhook_openapi_spec_json(
    db: AsyncSession = Depends(get_db)
):
    """Get OpenAPI spec as downloadable JSON file."""
    spec = await get_webhook_openapi_spec(db)
    
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=spec,
        headers={"Content-Disposition": "attachment; filename=webhook-openapi-spec.json"}
    )


@router.get("/webhooks.yaml")
async def get_webhook_openapi_spec_yaml(
    db: AsyncSession = Depends(get_db)
):
    """Get OpenAPI spec as downloadable YAML file."""
    spec = await get_webhook_openapi_spec(db)
    
    try:
        import yaml
        yaml_content = yaml.dump(spec, default_flow_style=False, sort_keys=False)
        
        from fastapi.responses import Response
        return Response(
            content=yaml_content,
            media_type="application/x-yaml",
            headers={"Content-Disposition": "attachment; filename=webhook-openapi-spec.yaml"}
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PyYAML not installed. Cannot generate YAML format."
        )