"""Dynamic OpenAPI specification generator for runtime API."""
from typing import Any

from fastapi.openapi.utils import get_openapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.function import Function
from app.models.webhook import Webhook


async def generate_runtime_openapi(db: AsyncSession) -> dict[str, Any]:
    """
    Generate dynamic OpenAPI specification for runtime API.

    Merges FastAPI's auto-generated spec with dynamic endpoints for:
    - Active webhooks (based on database)
    - Active agents (based on database)
    """
    from app.api.runtime import runtime_router

    # Get FastAPI's auto-generated OpenAPI spec for static endpoints
    base_spec = get_openapi(
        title="SINAS Runtime API",
        version="1.0.0",
        description="Execute AI agents, webhooks, and continue conversations",
        routes=runtime_router.routes,
    )

    # Ensure we have the paths dict
    if "paths" not in base_spec:
        base_spec["paths"] = {}

    # Ensure we have security schemes
    if "components" not in base_spec:
        base_spec["components"] = {}
    if "securitySchemes" not in base_spec["components"]:
        base_spec["components"]["securitySchemes"] = {
            "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        }

    # Add dynamic webhook endpoints (database-driven)
    webhook_result = await db.execute(select(Webhook).where(Webhook.is_active == True))
    webhooks = webhook_result.scalars().all()

    for webhook in webhooks:
        # Load associated function to get schemas
        function = await Function.get_by_name(db, webhook.function_namespace, webhook.function_name)

        path = f"/webhooks/{webhook.path}"
        method = webhook.http_method.lower()

        if path not in base_spec["paths"]:
            base_spec["paths"][path] = {}

        base_spec["paths"][path][method] = {
            "summary": webhook.description
            or f"Execute {webhook.function_namespace}/{webhook.function_name}",
            "tags": ["runtime-webhooks"],
            "operationId": f"execute_webhook_{webhook.path.replace('/', '_')}_{method}",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": function.input_schema
                        if function and function.input_schema
                        else {"type": "object"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Function executed successfully",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "success": {"type": "boolean"},
                                    "execution_id": {"type": "string", "format": "uuid"},
                                    "result": function.output_schema
                                    if function and function.output_schema
                                    else {"type": "object"},
                                },
                            }
                        }
                    },
                },
                "401": {"description": "Authentication required"},
                "404": {"description": "Webhook not found"},
                "500": {"description": "Function execution failed"},
            },
        }

        # Add security if required
        if webhook.requires_auth:
            base_spec["paths"][path][method]["security"] = [{"BearerAuth": []}, {"ApiKeyAuth": []}]

    # Add dynamic agent chat creation endpoints (database-driven)
    agent_result = await db.execute(select(Agent).where(Agent.is_active == True))
    agents = agent_result.scalars().all()

    for agent in agents:
        path = f"/agents/{agent.namespace}/{agent.name}/chats"

        # Build request schema for chat creation with optional input
        input_schema_props = agent.input_schema if agent.input_schema else {"type": "object"}
        request_schema = {
            "type": "object",
            "properties": {
                "input": {
                    **input_schema_props,
                    "description": "Structured input for system prompt templating (validated against agent's input_schema)",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title for the chat (defaults to 'Chat with {namespace}/{name}')",
                },
            },
        }

        base_spec["paths"][path] = {
            "post": {
                "summary": f"Create chat with {agent.namespace}/{agent.name}",
                "description": f"Create new chat with the {agent.namespace}/{agent.name} agent. Returns chat object. Use POST /chats/{{chat_id}}/messages to send messages.",
                "tags": ["runtime-agents"],
                "operationId": f"create_chat_with_{agent.namespace}_{agent.name}".replace(
                    "-", "_"
                ).replace(".", "_"),
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": request_schema}},
                },
                "responses": {
                    "200": {
                        "description": "Chat created successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string", "format": "uuid"},
                                        "user_id": {"type": "string", "format": "uuid"},
                                        "group_id": {"type": "string", "format": "uuid"},
                                        "agent_id": {"type": "string", "format": "uuid"},
                                        "agent_namespace": {"type": "string"},
                                        "agent_name": {"type": "string"},
                                        "title": {"type": "string"},
                                        "created_at": {"type": "string", "format": "date-time"},
                                        "updated_at": {"type": "string", "format": "date-time"},
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Input validation failed"},
                    "404": {"description": "Agent not found"},
                },
                "security": [],  # Optional auth
            }
        }

    # All other endpoints (chats, auth, states, executions) are auto-generated by FastAPI
    return base_spec
