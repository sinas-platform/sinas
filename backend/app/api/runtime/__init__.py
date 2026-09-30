"""Runtime API - Data Plane for execution, authentication, and runtime state."""
from fastapi import APIRouter

from app.api.runtime.endpoints import batches, manifests, authentication, chats, components, discovery, executions, files, functions, info, oidc, pipelines, queries, stores, templates, webhooks

runtime_router = APIRouter()

# Mount runtime endpoints
# Info - public instance info (auth mode, version) for SDK/client discovery
runtime_router.include_router(info.router, tags=["runtime-info"])

# Auth - OTP, tokens, API keys
runtime_router.include_router(authentication.router, prefix="/auth", tags=["runtime-auth"])

# OIDC-compatible verification - JWKS (root-level .well-known) + userinfo
runtime_router.include_router(oidc.router, tags=["runtime-oidc"])

# Chats - agent chat creation, message execution, and chat management
runtime_router.include_router(chats.router, tags=["runtime-chats"])

# Functions - function execution (sync and async)
runtime_router.include_router(functions.router, tags=["runtime-functions"])

# Webhooks - HTTP webhook execution
runtime_router.include_router(webhooks.router, prefix="/webhooks", tags=["runtime-webhooks"])

# Queries - query execution
runtime_router.include_router(queries.router, tags=["runtime-queries"])

# Pipelines - manual runs, run history, replay
runtime_router.include_router(pipelines.router, tags=["runtime-pipelines"])

# Executions - function execution history and status
runtime_router.include_router(executions.router, tags=["runtime-executions"])

# Stores - store-based state access
runtime_router.include_router(stores.router, tags=["runtime-stores"])

# Files - file upload, download, and management
runtime_router.include_router(files.router, prefix="/files", tags=["runtime-files"])

# Templates - template rendering and email sending
runtime_router.include_router(templates.router, tags=["runtime-templates"])

# Manifests - manifest status validation
runtime_router.include_router(manifests.router, tags=["runtime-manifests"])

# Components - render and proxy
runtime_router.include_router(components.router, tags=["runtime-components"])

# Discovery - list resources visible to the current user
runtime_router.include_router(discovery.router, tags=["runtime-discovery"])

# Batches - submit/poll/cancel function and agent batches
runtime_router.include_router(batches.router, tags=["runtime-batches"])
