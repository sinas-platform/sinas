from fastapi import APIRouter

from app.api import subtenants, functions, webhooks, webhook_handler, schedules, executions, packages, openapi_spec

router = APIRouter()

# Include all route modules
router.include_router(subtenants.router)
router.include_router(functions.router)
router.include_router(webhooks.router)
router.include_router(webhook_handler.router)
router.include_router(schedules.router)
router.include_router(executions.router)
router.include_router(packages.router)
router.include_router(openapi_spec.router)