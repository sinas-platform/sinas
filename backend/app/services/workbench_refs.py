"""Workbench file references in tool calls, and result spill.

Two halves of one idea: file content should not have to travel through the
model.

Outbound — references: a tool-call parameter whose value is the typed
object {"$workbench": "<path>"} (optionally {"encoding": "text"|"base64"})
is resolved by the tool executor at dispatch time: the file's current
version is loaded from the calling chat's workbench and the object is
replaced by the content. The model passes a reference; the tool receives
bytes. Works for every tool kind with zero per-service code, and the
typed-object sentinel cannot collide with legitimate string content.

Inbound — spill: a tool result too large for the context is saved in full
to the chat's workbench (tool_results/<tool>_<call-id>…) before the inline
copy is truncated, and the truncated copy carries a pointer. The model can
then read the full result with workbench_read (offsets) or process it with
code execution — and, unlike before, the full bytes actually survive
(truncation used to run before any persistence).

Resolution is chat-scoped by construction: only the calling chat's own
workbench is reachable. Design: the workbench file-references ADR.
"""
import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import select

from app.core.config import settings

logger = logging.getLogger(__name__)

SENTINEL_KEY = "$workbench"
_ALLOWED_KEYS = {SENTINEL_KEY, "encoding"}

_TEXTUAL_TYPES = ("text/",)
_TEXTUAL_EXACT = {
    "application/json",
    "application/x-yaml",
    "application/sql",
    "application/javascript",
    "application/typescript",
    "application/xml",
}


class ReferenceError_(Exception):
    """A workbench reference could not be resolved (bad path, missing file,
    over the size cap). The whole tool call fails with this message — a
    sentinel must never leak through to the tool as literal arguments."""


def contains_reference(arguments_str: Any) -> bool:
    """Cheap pre-check so the common case (no references) costs nothing."""
    if isinstance(arguments_str, str):
        return f'"{SENTINEL_KEY}"' in arguments_str
    if isinstance(arguments_str, (dict, list)):
        return f'"{SENTINEL_KEY}"' in json.dumps(arguments_str)
    return False


def _is_reference(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and SENTINEL_KEY in value
        and set(value.keys()) <= _ALLOWED_KEYS
        and isinstance(value[SENTINEL_KEY], str)
    )


def _default_encoding(content_type: str) -> str:
    if content_type.startswith(_TEXTUAL_TYPES) or content_type in _TEXTUAL_EXACT:
        return "text"
    return "base64"


async def resolve_references(db, chat, user_id: str, arguments: Any) -> Any:
    """Replace every {"$workbench": path} object in `arguments` with the
    referenced file's content. Raises ReferenceError_ on any failure."""
    from app.services.workbench import _validate_path, get_or_create_workbench
    from app.models.file import File, FileVersion
    from app.services.file_storage import get_storage

    if chat is None:
        raise ReferenceError_("Workbench references require a chat context")
    if str(chat.user_id) != str(user_id):
        raise ReferenceError_("Workbench belongs to a different user")

    workbench = await get_or_create_workbench(db, chat)
    storage = get_storage()

    async def _resolve_one(ref: dict[str, Any]) -> str:
        path = ref[SENTINEL_KEY]
        err = _validate_path(path)
        if err:
            raise ReferenceError_(f"Invalid workbench reference {path!r}: {err}")
        f = (
            await db.execute(
                select(File).where(File.collection_id == workbench.id, File.name == path)
            )
        ).scalar_one_or_none()
        if not f:
            raise ReferenceError_(
                f"Workbench reference {path!r} not found — list files with workbench_list"
            )
        version = (
            await db.execute(
                select(FileVersion).where(
                    FileVersion.file_id == f.id,
                    FileVersion.version_number == f.current_version,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise ReferenceError_(f"Workbench reference {path!r} has no stored version")
        if version.size_bytes > settings.workbench_ref_max_bytes:
            raise ReferenceError_(
                f"Workbench reference {path!r} is {version.size_bytes} bytes, above the "
                f"{settings.workbench_ref_max_bytes}-byte reference limit"
            )
        try:
            content = await storage.read(version.storage_path)
        except Exception as e:
            raise ReferenceError_(f"Failed to read workbench reference {path!r}: {e}")

        encoding = ref.get("encoding") or _default_encoding(f.content_type)
        if encoding == "base64":
            import base64

            return base64.b64encode(content).decode()
        if encoding == "text":
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                raise ReferenceError_(
                    f"Workbench reference {path!r} is not valid UTF-8 text — "
                    'pass {"encoding": "base64"} in the reference'
                )
        raise ReferenceError_(
            f"Unknown encoding {encoding!r} in workbench reference {path!r} "
            "(use 'text' or 'base64')"
        )

    async def _walk(node: Any) -> Any:
        if _is_reference(node):
            return await _resolve_one(node)
        if isinstance(node, dict):
            return {k: await _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [await _walk(v) for v in node]
        return node

    return await _walk(arguments)


def _spill_filename(tool_name: str, tool_call_id: str, content: str) -> str:
    safe_tool = re.sub(r"[^A-Za-z0-9_-]", "_", tool_name)[:60]
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", tool_call_id)[:16]
    try:
        json.loads(content)
        ext = "json"
    except (ValueError, TypeError):
        ext = "txt"
    return f"tool_results/{safe_tool}_{safe_id}.{ext}"


async def spill_result(
    db, chat, user_id: str, tool_name: str, tool_call_id: str, content: str
) -> Optional[str]:
    """Save a full oversized tool result into the chat's workbench.

    Returns the workbench path, or None when spilling isn't possible (no
    chat, workbench not enabled for the agent, write failure) — in which
    case the caller keeps today's truncate-only behavior.
    """
    from app.services.collection_tools import _infer_content_type
    from app.services.file_storage import get_storage
    from app.services.workbench import (
        _write_bytes,
        chat_has_workbench_enabled,
        get_or_create_workbench,
    )

    try:
        if chat is None or str(chat.user_id) != str(user_id):
            return None
        if not await chat_has_workbench_enabled(db, chat):
            return None
        workbench = await get_or_create_workbench(db, chat)
        path = _spill_filename(tool_name, tool_call_id, content)
        result = await _write_bytes(
            db,
            get_storage(),
            workbench,
            filename=path,
            content=content.encode("utf-8"),
            content_type=_infer_content_type(path),
            user_id=str(chat.user_id),
            visibility="private",
            file_metadata={"origin": "tool", "tool_name": tool_name, "tool_call_id": tool_call_id},
        )
        if "error" in result:
            logger.warning(f"Result spill failed for {tool_name}: {result['error']}")
            return None
        await db.commit()
        return path
    except Exception as e:
        logger.warning(f"Result spill failed for {tool_name}: {e}")
        return None


def attach_spill_pointer(truncated_content: str, path: str) -> str:
    """Point the truncated inline result at the spilled workbench file."""
    note = (
        f"Full result saved to workbench file '{path}' — read it with "
        "workbench_read (supports offset/limit) or process it with code execution."
    )
    try:
        parsed = json.loads(truncated_content)
    except (ValueError, TypeError):
        return truncated_content + f"\n[{note}]"
    if isinstance(parsed, dict):
        parsed["_full_result"] = {"workbench_file": path, "hint": note}
        return json.dumps(parsed)
    return truncated_content + f"\n[{note}]"
