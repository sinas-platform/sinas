"""
Canonical registry of all permissions evaluated in the codebase.

Each resource lists its available actions. The UI composes the full
permission string: ``sinas.{resource}.{action}:{scope}``

For namespaced resources the pattern is:
  ``sinas.{resource}/{namespace}/{name}.{action}:{scope}``
or with wildcards:
  ``sinas.{resource}/*/*.{action}:{scope}``

Scope (own/all) is chosen by the user when granting — it is NOT
part of this registry.
"""

from typing import Any

# Each entry describes a permission resource.
#   resource  — the dotted resource identifier (after "sinas.")
#   actions   — list of action verbs evaluated in the code
#   namespaced — True if the resource uses /namespace/name paths
#   adminOnly — True if ALL actions require :all scope
#   description — short human label
PERMISSION_REGISTRY: list[dict[str, Any]] = [
    # --- Core resources (user-facing) ---
    {
        "resource": "agents",
        "description": "AI agents",
        "actions": ["create", "read", "update", "delete", "chat"],
        "namespaced": True,
    },
    {
        "resource": "functions",
        "description": "Executable functions",
        "actions": ["create", "read", "update", "delete", "execute"],
        "namespaced": True,
    },
    {
        "resource": "skills",
        "description": "Reusable instruction modules",
        "actions": ["create", "read", "update", "delete"],
        "namespaced": True,
    },
    {
        "resource": "states",
        "description": "Key-value state storage",
        "actions": ["create", "read", "update", "delete"],
        "namespaced": True,
        "note": "Namespaced by state namespace only (no /name)",
    },
    {
        "resource": "collections",
        "description": "File collections",
        "actions": ["create", "read", "update", "delete", "upload", "download", "list", "delete_files"],
        "namespaced": True,
    },
    {
        "resource": "templates",
        "description": "Email/render templates",
        "actions": ["create", "read", "update", "delete", "render", "send"],
        "namespaced": True,
    },
    {
        "resource": "webhooks",
        "description": "HTTP webhook triggers",
        "actions": ["create", "read", "update", "delete"],
    },
    {
        "resource": "schedules",
        "description": "Cron-based scheduled jobs",
        "actions": ["create", "read", "update", "delete"],
    },
    {
        "resource": "executions",
        "description": "Execution & message history",
        "actions": ["read", "update"],
    },
    # --- User / auth resources ---
    {
        "resource": "users",
        "description": "User accounts",
        "actions": ["create", "read", "update"],
    },
    {
        "resource": "api_keys",
        "description": "API key management",
        "actions": ["create", "read", "delete"],
    },
    {
        "resource": "roles",
        "description": "Role & permission management",
        "actions": ["create", "read", "update", "delete", "manage_members", "manage_permissions"],
        "adminOnly": True,
    },
    {
        "resource": "logs",
        "description": "Request audit logs",
        "actions": ["read"],
    },
    # --- Admin-only infrastructure ---
    {
        "resource": "llm_providers",
        "description": "LLM provider configuration",
        "actions": ["create", "read", "update", "delete"],
        "adminOnly": True,
    },
    {
        "resource": "packages",
        "description": "Python package management",
        "actions": ["install", "read", "delete"],
        "adminOnly": True,
    },
    {
        "resource": "config",
        "description": "Declarative YAML configuration",
        "actions": ["validate", "apply", "read"],
        "adminOnly": True,
    },
    {
        "resource": "system",
        "description": "Infrastructure (queues, workers, containers)",
        "actions": ["read", "update"],
        "adminOnly": True,
    },
]
