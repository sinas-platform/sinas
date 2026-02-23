"""API v1 router - Management API (Control Plane for configuration)."""
from fastapi import APIRouter

from .endpoints import (
    agents,
    api_keys,
    apps,
    collections,
    config,
    containers,
    functions,
    llm_providers,

    messages,
    packages,
    queue,
    request_logs,
    roles,
    schedules,
    skills,
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
router.include_router(collections.router)
router.include_router(llm_providers.router, prefix="/llm-providers", tags=["llm-providers"])

router.include_router(roles.router)
router.include_router(users.router)
router.include_router(api_keys.router, tags=["api-keys"])
router.include_router(templates.router, prefix="/templates", tags=["templates"])

# Function configuration routes
router.include_router(functions.router)
router.include_router(webhooks.router)
router.include_router(packages.router)
router.include_router(schedules.router)

# Observability routes
router.include_router(messages.router)
router.include_router(request_logs.router)

# System routes
router.include_router(containers.router)
router.include_router(workers.router)
router.include_router(queue.router)

# Configuration routes
router.include_router(config.router, prefix="/config", tags=["config"])
