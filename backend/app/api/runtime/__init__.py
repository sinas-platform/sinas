"""Runtime API - Data Plane for execution, authentication, and runtime state."""
from fastapi import APIRouter

from app.api.runtime.endpoints import apps, authentication, chats, discovery, executions, files, jobs, states, templates, webhooks

runtime_router = APIRouter()

# Mount runtime endpoints
# Auth - OTP, tokens, API keys
runtime_router.include_router(authentication.router, prefix="/auth", tags=["runtime-auth"])

# Chats - agent chat creation, message execution, and chat management
runtime_router.include_router(chats.router, tags=["runtime-chats"])

# Webhooks - HTTP webhook execution
runtime_router.include_router(webhooks.router, prefix="/webhooks", tags=["runtime-webhooks"])

# Executions - function execution history and status
runtime_router.include_router(executions.router, tags=["runtime-executions"])

# Jobs - queue job status and results
runtime_router.include_router(jobs.router, tags=["runtime-jobs"])

# States - runtime state storage
runtime_router.include_router(states.router, tags=["runtime-states"])

# Files - file upload, download, and management
runtime_router.include_router(files.router, prefix="/files", tags=["runtime-files"])

# Templates - template rendering and email sending
runtime_router.include_router(templates.router, tags=["runtime-templates"])

# Apps - app status validation
runtime_router.include_router(apps.router, tags=["runtime-apps"])

# Discovery - list resources visible to the current user
runtime_router.include_router(discovery.router, tags=["runtime-discovery"])
