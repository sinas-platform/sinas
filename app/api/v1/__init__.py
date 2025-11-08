"""API v1 router."""
from fastapi import APIRouter
from .endpoints import (
    authentication,
    chats,
    assistants,
    mcp_servers,
    groups,
    users,
    functions,
    webhooks,
    webhook_handler,
    executions,
    packages,
    schedules,
    request_logs,
    contexts,
    ontology_datasources,
    ontology_concepts,
    ontology_properties,
    ontology_queries,
    ontology_endpoints,
    ontology_execute,
    ontology_data,
    ontology_records,
    email_templates,
    emails,
    email_inboxes,
)

router = APIRouter()

# Core routes
router.include_router(authentication.router, prefix="/auth", tags=["authentication"])
router.include_router(chats.router, prefix="/chats", tags=["chats"])
router.include_router(assistants.router, prefix="/assistants", tags=["assistants"])
router.include_router(mcp_servers.router, prefix="/mcp", tags=["mcp"])
router.include_router(groups.router)
router.include_router(users.router)

# Function execution routes
router.include_router(functions.router)
router.include_router(webhooks.router)
router.include_router(webhook_handler.router)
router.include_router(executions.router)
router.include_router(packages.router)
router.include_router(schedules.router)

# Logging routes
router.include_router(request_logs.router)

# Context Store routes
router.include_router(contexts.router)

# Ontology routes
router.include_router(ontology_datasources.router)
router.include_router(ontology_concepts.router)
router.include_router(ontology_properties.property_router)
router.include_router(ontology_properties.relationship_router)
router.include_router(ontology_queries.router)
router.include_router(ontology_endpoints.router)
router.include_router(ontology_execute.router)
router.include_router(ontology_data.router)
router.include_router(ontology_records.router)

# Email routes
router.include_router(email_templates.router)
router.include_router(emails.router)
router.include_router(email_inboxes.router)
