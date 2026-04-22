"""Sinas package management tools for agents.

Exposes the existing PackageService and ConfigParser as LLM tools for agents
that have opted in via `system_tools: ["packageManagement"]`. Nothing here
duplicates validation/apply logic — these are thin adapters.

Permissions are still enforced: every tool checks the same permission that
the corresponding HTTP endpoint checks, using the acting user's permissions.
"""
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_permission
from app.services.config_parser import ConfigParser
from app.services.package_service import PackageService

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Tool definitions (OpenAI function-calling format)
#
# Tool names are prefixed with "sinas_package_" so they don't collide
# with regular user-defined functions (which use "namespace__name").
# ─────────────────────────────────────────────────────────────

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "sinas_package_validate",
            "description": (
                "Validate a Sinas package YAML without applying it. Checks "
                "YAML syntax, schema conformance, and references. Returns "
                "{valid, errors, warnings}. Preferred: pass file_path to "
                "read from a collection file (avoids large inline YAML). "
                "Does NOT make any changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to YAML file in a collection: 'namespace/collection/filename'. Preferred over inline yaml.",
                    },
                    "yaml": {
                        "type": "string",
                        "description": "Inline YAML content. Use file_path instead when the file is already saved.",
                    },
                },
            },
            "_metadata": {"system_tool": "packageManagement"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_package_preview",
            "description": (
                "Dry-run a package install. Shows what resources would be "
                "created, updated, or skipped without making any changes. "
                "Call this before sinas_package_install to let the user "
                "see the planned changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to YAML file in a collection: 'namespace/collection/filename'. Preferred over inline yaml.",
                    },
                    "yaml": {
                        "type": "string",
                        "description": "Inline YAML content. Use file_path instead when the file is already saved.",
                    },
                },
            },
            "_metadata": {"system_tool": "packageManagement"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_package_install",
            "description": (
                "Install a Sinas package. This WRITES to the database: "
                "creates/updates resources and records a Package row. "
                "The user will be asked to approve the install before it "
                "runs. Always call sinas_package_preview first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to YAML file in a collection: 'namespace/collection/filename'. Preferred over inline yaml.",
                    },
                    "yaml": {
                        "type": "string",
                        "description": "Inline YAML content. Use file_path instead when the file is already saved.",
                    },
                },
            },
            "_metadata": {
                "system_tool": "packageManagement",
                "requires_approval": True,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_package_uninstall",
            "description": (
                "Uninstall a previously installed package by name. Deletes "
                "all resources tagged with managed_by='pkg:<name>'. The "
                "user will be asked to approve before it runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package_name": {
                        "type": "string",
                        "description": "The name of the package to uninstall.",
                    },
                },
                "required": ["package_name"],
            },
            "_metadata": {
                "system_tool": "packageManagement",
                "requires_approval": True,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_package_list",
            "description": (
                "List all installed packages with their name, version, "
                "description, author, and install date."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "_metadata": {"system_tool": "packageManagement"},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sinas_package_export",
            "description": (
                "Export an installed package back to YAML. Useful for "
                "reading the current state of a package or using it as "
                "a reference/template for drafting a new one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package_name": {
                        "type": "string",
                        "description": "The name of the package to export.",
                    },
                },
                "required": ["package_name"],
            },
            "_metadata": {"system_tool": "packageManagement"},
        },
    },
]


def get_package_tool_definitions() -> list[dict[str, Any]]:
    """Return the list of package management tool definitions."""
    return [t.copy() for t in _TOOL_DEFINITIONS]


PACKAGE_TOOL_NAMES = {t["function"]["name"] for t in _TOOL_DEFINITIONS}


def is_package_tool(tool_name: str) -> bool:
    """Check if a tool name is a Sinas package management tool."""
    return tool_name in PACKAGE_TOOL_NAMES


# ─────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────

async def execute_package_tool(
    db: AsyncSession,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    permissions: dict[str, bool],
    agent_system_tools: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Dispatch a package management tool call.

    Returns a JSON-serializable result dict. On error, returns
    {"error": str, "detail": str} rather than raising, so the LLM can
    reason about the failure.
    """
    # Gate 1: the agent must have packageManagement in its system_tools
    if "packageManagement" not in (agent_system_tools or []):
        return {
            "error": "capability_not_enabled",
            "detail": (
                "This agent does not have 'packageManagement' in its "
                "systemTools list. An admin must enable it on the agent."
            ),
        }

    try:
        if tool_name == "sinas_package_validate":
            return await _validate(db, arguments, permissions)
        if tool_name == "sinas_package_preview":
            return await _preview(db, arguments, user_id, permissions)
        if tool_name == "sinas_package_install":
            return await _install(db, arguments, user_id, permissions)
        if tool_name == "sinas_package_uninstall":
            return await _uninstall(db, arguments, permissions)
        if tool_name == "sinas_package_list":
            return await _list(db, permissions)
        if tool_name == "sinas_package_export":
            return await _export(db, arguments, permissions)
        return {"error": "unknown_tool", "detail": f"Unknown package tool: {tool_name}"}
    except ValueError as e:
        return {"error": "validation_error", "detail": str(e)}
    except PermissionError as e:
        return {"error": "permission_denied", "detail": str(e)}
    except Exception as e:
        logger.error(f"Package tool {tool_name} failed: {e}", exc_info=True)
        return {"error": "internal_error", "detail": str(e)}


# ── File path resolver ──────────────────────────────────────

async def _resolve_yaml_content(db, arguments) -> str:
    """Resolve YAML content from either file_path or inline yaml argument.

    file_path format: 'namespace/collection/filename'
    """
    file_path = arguments.get("file_path")
    yaml_content = arguments.get("yaml", "")

    if file_path:
        from app.models.file import Collection, File, FileVersion
        from app.services.file_storage import get_storage
        from sqlalchemy import and_, select

        parts = file_path.split("/", 2)
        if len(parts) != 3:
            raise ValueError(f"file_path must be 'namespace/collection/filename', got: {file_path}")

        namespace, collection_name, filename = parts
        coll = await Collection.get_by_name(db, namespace, collection_name)
        if not coll:
            raise ValueError(f"Collection '{namespace}/{collection_name}' not found")

        result = await db.execute(
            select(File).where(
                and_(File.collection_id == coll.id, File.name == filename)
            ).limit(1)
        )
        file_record = result.scalar_one_or_none()
        if not file_record:
            raise ValueError(f"File '{filename}' not found in {namespace}/{collection_name}")

        ver_result = await db.execute(
            select(FileVersion).where(
                and_(
                    FileVersion.file_id == file_record.id,
                    FileVersion.version_number == file_record.current_version,
                )
            )
        )
        file_version = ver_result.scalar_one_or_none()
        if not file_version:
            raise ValueError(f"Version not found for file '{filename}'")

        storage = get_storage()
        raw = await storage.read(file_version.storage_path)
        yaml_content = raw.decode("utf-8")

    if not yaml_content:
        raise ValueError("Either 'file_path' or 'yaml' is required")

    return yaml_content


# ── Individual tool implementations ──────────────────────────

async def _validate(db, arguments, permissions):
    if not check_permission(permissions, "sinas.config.validate:all"):
        raise PermissionError("sinas.config.validate:all required")

    yaml_content = await _resolve_yaml_content(db, arguments)

    _config, validation = await ConfigParser.parse_and_validate(
        yaml_content, db=db, strict=False
    )
    return {
        "valid": validation.is_valid,
        "errors": [{"path": e.path, "message": e.message} for e in validation.errors],
        "warnings": list(validation.warnings),
    }


async def _preview(db, arguments, user_id, permissions):
    if not check_permission(permissions, "sinas.packages.install:all"):
        raise PermissionError("sinas.packages.install:all required")

    yaml_content = await _resolve_yaml_content(db, arguments)

    service = PackageService(db)
    result = await service.preview(yaml_content, user_id)
    return _apply_response_to_dict(result)


async def _install(db, arguments, user_id, permissions):
    if not check_permission(permissions, "sinas.packages.install:all"):
        raise PermissionError("sinas.packages.install:all required")

    yaml_content = await _resolve_yaml_content(db, arguments)

    service = PackageService(db)
    package, result = await service.install(yaml_content, user_id)
    return {
        "package": {
            "name": package.name,
            "version": package.version,
            "description": package.description,
            "author": package.author,
            "installed_at": package.installed_at.isoformat() if package.installed_at else None,
        },
        "apply": _apply_response_to_dict(result),
    }


async def _uninstall(db, arguments, permissions):
    if not check_permission(permissions, "sinas.packages.uninstall:all"):
        raise PermissionError("sinas.packages.uninstall:all required")

    package_name = arguments.get("package_name", "")
    if not package_name:
        return {"error": "missing_package_name", "detail": "'package_name' argument is required"}

    service = PackageService(db)
    deleted = await service.uninstall(package_name)
    return {"package_name": package_name, "deleted": deleted}


async def _list(db, permissions):
    if not check_permission(permissions, "sinas.packages.read:own"):
        raise PermissionError("sinas.packages.read:own required")

    service = PackageService(db)
    packages = await service.list_packages()
    return {
        "packages": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "author": p.author,
                "source_url": p.source_url,
                "installed_at": p.installed_at.isoformat() if p.installed_at else None,
            }
            for p in packages
        ]
    }


async def _export(db, arguments, permissions):
    if not check_permission(permissions, "sinas.packages.read:own"):
        raise PermissionError("sinas.packages.read:own required")

    package_name = arguments.get("package_name", "")
    if not package_name:
        return {"error": "missing_package_name", "detail": "'package_name' argument is required"}

    service = PackageService(db)
    yaml_content = await service.export_package(package_name)
    return {"package_name": package_name, "yaml": yaml_content}


def _apply_response_to_dict(result) -> dict[str, Any]:
    """Convert a ConfigApplyResponse to a plain dict for JSON serialization."""
    summary = result.summary
    return {
        "success": result.success,
        "summary": {
            "created": summary.created,
            "updated": summary.updated,
            "unchanged": summary.unchanged,
            "deleted": summary.deleted,
        },
        "changes": [
            {
                "action": c.action,
                "resourceType": c.resourceType,
                "resourceName": c.resourceName,
                "details": c.details,
            }
            for c in (result.changes or [])
        ],
        "errors": list(result.errors or []),
        "warnings": list(result.warnings or []),
    }
