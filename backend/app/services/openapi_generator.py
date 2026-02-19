"""Dynamic OpenAPI specification generator for runtime API."""
from typing import Any

from fastapi.openapi.utils import get_openapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.file import Collection
from app.models.function import Function
from app.models.template import Template
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

    # Add dynamic collection upload endpoints (database-driven)
    collection_result = await db.execute(select(Collection))
    collections = collection_result.scalars().all()

    for coll in collections:
        path = f"/files/{coll.namespace}/{coll.name}"

        # Build file_metadata schema from collection's metadata_schema
        metadata_schema = coll.metadata_schema if coll.metadata_schema else {"type": "object"}

        upload_schema = {
            "type": "object",
            "required": ["name", "content_base64", "content_type"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Filename (must be unique within collection)",
                },
                "content_base64": {
                    "type": "string",
                    "description": "File content encoded as base64",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type (e.g., image/png, text/plain)",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "shared"],
                    "default": "private",
                    "description": "File visibility",
                },
                "file_metadata": {
                    **metadata_schema,
                    "description": "File metadata (validated against collection schema)",
                },
            },
        }

        op_id = f"upload_to_{coll.namespace}_{coll.name}".replace("-", "_").replace(".", "_")

        base_spec["paths"][path] = {
            "post": {
                "summary": f"Upload file to {coll.namespace}/{coll.name}",
                "description": f"Upload a file to the {coll.namespace}/{coll.name} collection. "
                f"Max file size: {coll.max_file_size_mb}MB.",
                "tags": ["runtime-files"],
                "operationId": op_id,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": upload_schema}},
                },
                "responses": {
                    "201": {
                        "description": "File uploaded successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string", "format": "uuid"},
                                        "namespace": {"type": "string"},
                                        "name": {"type": "string"},
                                        "content_type": {"type": "string"},
                                        "current_version": {"type": "integer"},
                                        "file_metadata": metadata_schema,
                                        "visibility": {"type": "string"},
                                        "created_at": {"type": "string", "format": "date-time"},
                                        "updated_at": {"type": "string", "format": "date-time"},
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Validation failed (metadata, content filter)"},
                    "403": {"description": "Not authorized"},
                    "413": {"description": "File too large or storage quota exceeded"},
                },
                "security": [{"BearerAuth": []}, {"ApiKeyAuth": []}],
            },
        }

    # Add dynamic template render + email endpoints (database-driven)
    template_result = await db.execute(select(Template).where(Template.is_active == True))
    templates = template_result.scalars().all()

    for tmpl in templates:
        variables_schema = tmpl.variable_schema if tmpl.variable_schema else {"type": "object"}
        safe_name = f"{tmpl.namespace}_{tmpl.name}".replace("-", "_").replace(".", "_")

        # Render endpoint
        render_path = f"/templates/{tmpl.namespace}/{tmpl.name}/render"
        base_spec["paths"][render_path] = {
            "post": {
                "summary": f"Render template {tmpl.namespace}/{tmpl.name}",
                "description": tmpl.description or f"Render the {tmpl.namespace}/{tmpl.name} template with variables.",
                "tags": ["runtime-templates"],
                "operationId": f"render_template_{safe_name}",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["variables"],
                                "properties": {
                                    "variables": {
                                        **variables_schema,
                                        "description": "Template variables",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Template rendered successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string", "nullable": True},
                                        "html_content": {"type": "string"},
                                        "text_content": {"type": "string", "nullable": True},
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Variable validation or rendering failed"},
                    "403": {"description": "Not authorized"},
                    "404": {"description": "Template not found"},
                },
                "security": [{"BearerAuth": []}, {"ApiKeyAuth": []}],
            },
        }

        # Email endpoint
        email_path = f"/templates/{tmpl.namespace}/{tmpl.name}/email"
        base_spec["paths"][email_path] = {
            "post": {
                "summary": f"Send email with {tmpl.namespace}/{tmpl.name}",
                "description": f"Send an email using the {tmpl.namespace}/{tmpl.name} template.",
                "tags": ["runtime-templates"],
                "operationId": f"send_email_{safe_name}",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["to", "variables"],
                                "properties": {
                                    "to": {
                                        "type": "string",
                                        "format": "email",
                                        "description": "Recipient email address",
                                    },
                                    "from_alias": {
                                        "type": "string",
                                        "description": "From address alias (e.g., 'support' -> support@domain.com)",
                                    },
                                    "from_name": {
                                        "type": "string",
                                        "description": "Display name for sender (e.g., 'SINAS Support')",
                                    },
                                    "variables": {
                                        **variables_schema,
                                        "description": "Template variables",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Email sent successfully",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string"},
                                        "to": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Variable validation or rendering failed"},
                    "403": {"description": "Not authorized"},
                    "503": {"description": "Email service not configured"},
                },
                "security": [{"BearerAuth": []}, {"ApiKeyAuth": []}],
            },
        }

    # All other endpoints (chats, auth, states, executions) are auto-generated by FastAPI
    return base_spec
