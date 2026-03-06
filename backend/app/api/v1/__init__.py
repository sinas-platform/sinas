"""API v1 router - Management API (Control Plane for configuration)."""
from fastapi import APIRouter

from .endpoints import (
    agents,
    api_keys,
    apps,
    collections,
    components,
    config,
    containers,
    database_connections,
    database_schema,
    database_triggers,
    functions,
    llm_providers,

    messages,
    dependencies,
    packages,
    queries,
    queue,
    request_logs,
    roles,
    schedules,
    skills,
    stores,
    templates,
    users,
    webhooks,
    workers,
)

router = APIRouter()

# Core configuration routes
router.include_router(agents.router, prefix="/agents", tags=["agents"])
router.include_router(apps.router)
router.include_router(skills.router)
router.include_router(components.router)
router.include_router(collections.router)
router.include_router(stores.router)
router.include_router(llm_providers.router, prefix="/llm-providers", tags=["llm-providers"])
router.include_router(database_connections.router, prefix="/database-connections", tags=["database-connections"])
router.include_router(database_schema.router, prefix="/database-connections", tags=["database-schema"])
router.include_router(queries.router)

router.include_router(roles.router)
router.include_router(users.router)
router.include_router(api_keys.router, tags=["api-keys"])
router.include_router(templates.router, prefix="/templates", tags=["templates"])

# Function configuration routes
router.include_router(functions.router)
router.include_router(webhooks.router)
router.include_router(dependencies.router)
router.include_router(schedules.router)
router.include_router(database_triggers.router)

# Observability routes
router.include_router(messages.router)
router.include_router(request_logs.router)

# System routes
router.include_router(containers.router)
router.include_router(workers.router)
router.include_router(queue.router)

# Package routes
router.include_router(packages.router)

# Configuration routes
router.include_router(config.router, prefix="/config", tags=["config"])
