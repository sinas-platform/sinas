"""Runtime API - Data Plane for execution, authentication, and runtime state."""
from fastapi import APIRouter

from app.api.runtime.endpoints import authentication, chats, webhooks, states, executions

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

# States - runtime state storage
runtime_router.include_router(states.router, tags=["runtime-states"])
