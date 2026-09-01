"""Workbench: a chat's private working tree.

A workbench is the mutable file tree an agent works in for the duration of
a chat — scratchpad, artifact surface, and (once sandbox sync lands) the
tree code execution runs against. It is backed by a Collection row with
kind='workbench', which buys File/FileVersion versioning and storage for
free, but it is not a collection in the API sense: workbench rows carry an
internal namespace/name, are filtered out of every collections-API query
and permission resolution (Collection.get_by_name and friends filter
kind='collection'), and are reachable only through their chat.

Authorization model: reaching the chat IS the authorization. The tools
below are only ever bound for the chat's own conversation (tool discovery
is chat-scoped and runtime chat access is owner-checked), so no collection
permission is consulted for workbench-local operations. checkout/promote
touch real collections and enforce those collections' download/upload
permissions.

Every file in a workbench is visibility='private' and owned by the chat's
user. Design: backend/docs/adrs/2026-09-01-workbench-unified-file-tree-and-execution.md
"""
import logging
import uuid as uuid_lib
from fnmatch import fnmatch
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Chat
from app.models.file import Collection, File, FileVersion
from app.services.collection_tools import CollectionToolConverter
from app.services.file_storage import FileStorage, get_storage

logger = logging.getLogger(__name__)

WORKBENCH_KIND = "workbench"
# Internal identity only. Unreachable by name: every by-name resolver
# filters kind='collection'. The name is the chat id, so the (namespace,
# name) uniqueness constraint gives one workbench per chat.
_INTERNAL_NAMESPACE = "_chat"

# Bulk checkout bound — protects against "checkout */everything" surprises.
MAX_CHECKOUT_FILES = 1000

PROVENANCE_KEY = "workbench_source"  # file_metadata key carrying checkout provenance


def _validate_path(filename: str) -> Optional[str]:
    """Return an error string for unacceptable workbench paths, else None.

    Workbench filenames are relative paths ('notes.md', 'src/app.py').
    """
    if not filename or not filename.strip():
        return "filename is required"
    if len(filename) > 255:
        return "filename too long (max 255 characters)"
    if filename.startswith("/") or filename.startswith("\\"):
        return "filename must be a relative path"
    parts = filename.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "filename must not contain empty, '.' or '..' path segments"
    return None


async def get_or_create_workbench(db: AsyncSession, chat: Chat) -> Collection:
    """Get the chat's workbench, creating it on first use (race-safe)."""
    name = str(chat.id)
    stmt = select(Collection).where(
        Collection.kind == WORKBENCH_KIND,
        Collection.namespace == _INTERNAL_NAMESPACE,
        Collection.name == name,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    workbench = Collection(
        namespace=_INTERNAL_NAMESPACE,
        name=name,
        kind=WORKBENCH_KIND,
        user_id=chat.user_id,
        metadata_schema={},
        allow_shared_files=False,
        allow_private_files=True,
    )
    db.add(workbench)
    try:
        await db.flush()
    except IntegrityError:
        # Lost a creation race — the winner's row is what we want.
        await db.rollback()
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing
        raise
    return workbench


def get_workbench_tool_definitions() -> list[dict[str, Any]]:
    """Tool definitions for agents with system_tools: ["workbench"]."""

    def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
                "_metadata": {"tool_type": name},
            },
        }

    filename_prop = {
        "type": "string",
        "description": "Relative path within the workbench (e.g. 'notes.md', 'src/app.py')",
    }

    return [
        tool(
            "workbench_list",
            "List/search files in this chat's workbench — your private, persistent "
            "working tree. Files persist across turns of this conversation. "
            "Optionally filter by a content query or metadata.",
            {
                "query": {"type": "string", "description": "Optional text/regex query against file contents"},
                "metadata_filter": {"type": "object", "description": "Optional key-value metadata filter"},
            },
            [],
        ),
        tool(
            "workbench_read",
            "Read a file from the workbench. Text files return content inline with "
            "line numbers; binary files return a temporary URL. Use offset/limit "
            "for line ranges of large files.",
            {
                "filename": filename_prop,
                "offset": {"type": "integer", "description": "Start line (1-indexed)"},
                "limit": {"type": "integer", "description": "Max lines to return"},
            },
            ["filename"],
        ),
        tool(
            "workbench_write",
            "Write a file in the workbench (full contents; a new version is created "
            "if it exists). For targeted changes to an existing file use "
            "workbench_edit instead.",
            {
                "filename": filename_prop,
                "content": {"type": "string", "description": "Full file contents"},
            },
            ["filename", "content"],
        ),
        tool(
            "workbench_edit",
            "Edit a workbench file by exact string replacement. old_string must "
            "appear exactly once unless replace_all is true. Read the file first.",
            {
                "filename": filename_prop,
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"},
            },
            ["filename", "old_string", "new_string"],
        ),
        tool(
            "workbench_delete",
            "Delete a file from the workbench.",
            {"filename": filename_prop},
            ["filename"],
        ),
        tool(
            "workbench_checkout",
            "Copy files from a collection into the workbench to work on them. "
            "Pass 'path' for one file or 'pattern' (glob) for several; neither "
            "copies the whole collection. Checked-out files remember their "
            "source, so workbench_promote can offer updating the original.",
            {
                "collection": {"type": "string", "description": "Source collection as 'namespace/name'"},
                "path": {"type": "string", "description": "Exact filename to check out"},
                "pattern": {"type": "string", "description": "Glob pattern of filenames to check out (e.g. 'data/*.csv')"},
            },
            ["collection"],
        ),
        tool(
            "workbench_promote",
            "Publish a workbench file to a collection so it outlives this chat. "
            "A file that was checked out updates its source file (and reports a "
            "conflict if the source changed since checkout); other files are "
            "created in the target collection.",
            {
                "filename": filename_prop,
                "collection": {"type": "string", "description": "Target collection as 'namespace/name'"},
                "target_filename": {
                    "type": "string",
                    "description": "Filename in the target collection (defaults to the workbench filename)",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "shared"],
                    "description": "Visibility of a newly created target file (default 'private')",
                },
            },
            ["filename", "collection"],
        ),
    ]


class WorkbenchTools:
    """Executes workbench_* tool calls for a chat."""

    def __init__(self) -> None:
        self._conv = CollectionToolConverter()

    async def execute_tool(
        self,
        db: AsyncSession,
        chat: Chat,
        user_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if chat is None:
            return {"error": "Workbench tools require a chat context"}
        if str(chat.user_id) != str(user_id):
            # Tool discovery is chat-scoped so this should be unreachable;
            # belt and braces for the trust boundary.
            return {"error": "Workbench belongs to a different user"}

        workbench = await get_or_create_workbench(db, chat)

        if tool_name == "workbench_list":
            return await self._conv._search_collection(db, workbench, _INTERNAL_NAMESPACE, user_id, arguments)
        if tool_name == "workbench_read":
            return await self._conv._get_file(db, workbench, _INTERNAL_NAMESPACE, user_id, arguments)
        if tool_name == "workbench_write":
            err = _validate_path(arguments.get("filename", ""))
            if err:
                return {"error": err}
            return await self._conv._write_file(
                db, workbench, _INTERNAL_NAMESPACE, user_id, arguments, visibility="private"
            )
        if tool_name == "workbench_edit":
            err = _validate_path(arguments.get("filename", ""))
            if err:
                return {"error": err}
            return await self._conv._edit_file(db, workbench, _INTERNAL_NAMESPACE, user_id, arguments)
        if tool_name == "workbench_delete":
            return await self._conv._delete_file(db, workbench, _INTERNAL_NAMESPACE, user_id, arguments)
        if tool_name == "workbench_checkout":
            return await self._checkout(db, workbench, user_id, arguments)
        if tool_name == "workbench_promote":
            return await self._promote(db, workbench, user_id, arguments)
        return {"error": f"Unknown workbench tool: {tool_name}"}

    # ── checkout ────────────────────────────────────────────────────

    async def _checkout(
        self, db: AsyncSession, workbench: Collection, user_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        coll_ref = arguments.get("collection", "")
        if "/" not in coll_ref:
            return {"error": "collection must be 'namespace/name'"}
        namespace, name = coll_ref.split("/", 1)

        source = await Collection.get_by_name(db, namespace, name)
        if not source:
            return {"error": f"Collection '{coll_ref}' not found"}

        perm_err = await self._check_collection_perm(db, user_id, namespace, name, write=False)
        if perm_err:
            return perm_err

        path = arguments.get("path")
        pattern = arguments.get("pattern")

        # Candidate files: caller's own + shared — never other users' private
        # files, mirroring collection search visibility.
        stmt = select(File).where(
            File.collection_id == source.id,
            or_(File.user_id == uuid_lib.UUID(user_id), File.visibility == "shared"),
        )
        if path:
            stmt = stmt.where(File.name == path)
        files = (await db.execute(stmt)).scalars().all()
        if pattern and not path:
            files = [f for f in files if fnmatch(f.name, pattern)]

        if not files:
            target = path or pattern or "any files"
            return {"error": f"No accessible files matching {target!r} in '{coll_ref}'"}
        if len(files) > MAX_CHECKOUT_FILES:
            return {
                "error": (
                    f"Checkout of {len(files)} files exceeds the limit of "
                    f"{MAX_CHECKOUT_FILES}. Narrow with 'path' or 'pattern'."
                )
            }

        storage = get_storage()
        checked_out: list[dict[str, Any]] = []
        errors: list[str] = []
        for f in files:
            version = await self._current_version(db, f)
            if version is None:
                errors.append(f"{f.name}: no stored version")
                continue
            try:
                content = await storage.read(version.storage_path)
            except Exception as e:
                errors.append(f"{f.name}: read failed ({e})")
                continue
            provenance = {
                PROVENANCE_KEY: {
                    "collection": coll_ref,
                    "file_id": str(f.id),
                    "version": version.version_number,
                }
            }
            result = await _write_bytes(
                db,
                storage,
                workbench,
                filename=f.name,
                content=content,
                content_type=f.content_type,
                user_id=user_id,
                visibility="private",
                file_metadata=provenance,
            )
            if "error" in result:
                errors.append(f"{f.name}: {result['error']}")
            else:
                checked_out.append({"filename": f.name, "source_version": version.version_number})

        # Tool calls run in their own session that is discarded without a
        # commit (the reused converter tools commit internally) — persist
        # explicitly here.
        if checked_out:
            await db.commit()

        out: dict[str, Any] = {"collection": coll_ref, "checked_out": checked_out, "count": len(checked_out)}
        if errors:
            out["errors"] = errors
        return out

    # ── promote ─────────────────────────────────────────────────────

    async def _promote(
        self, db: AsyncSession, workbench: Collection, user_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        filename = arguments.get("filename", "")
        err = _validate_path(filename)
        if err:
            return {"error": err}
        coll_ref = arguments.get("collection", "")
        if "/" not in coll_ref:
            return {"error": "collection must be 'namespace/name'"}
        namespace, name = coll_ref.split("/", 1)

        target = await Collection.get_by_name(db, namespace, name)
        if not target:
            return {"error": f"Collection '{coll_ref}' not found"}

        perm_err = await self._check_collection_perm(db, user_id, namespace, name, write=True)
        if perm_err:
            return perm_err

        # Load the workbench file + current bytes
        wb_file = (
            await db.execute(
                select(File).where(File.collection_id == workbench.id, File.name == filename)
            )
        ).scalar_one_or_none()
        if not wb_file:
            return {"error": f"'{filename}' not found in the workbench"}
        wb_version = await self._current_version(db, wb_file)
        if wb_version is None:
            return {"error": f"'{filename}' has no stored version"}
        storage = get_storage()
        try:
            content = await storage.read(wb_version.storage_path)
        except Exception as e:
            return {"error": f"Failed to read '{filename}': {e}"}

        if target.content_filter_function:
            filter_err = await run_content_filter(
                db,
                target.content_filter_function,
                namespace=namespace,
                collection_name=name,
                filename=filename,
                content=content,
                content_type=wb_file.content_type,
                user_id=user_id,
            )
            if filter_err:
                return filter_err

        provenance = (wb_file.file_metadata or {}).get(PROVENANCE_KEY)
        target_filename = arguments.get("target_filename") or filename

        # Checked-out file going back to where it came from → update the source.
        if provenance and provenance.get("collection") == coll_ref and not arguments.get("target_filename"):
            source_file = (
                await db.execute(select(File).where(File.id == uuid_lib.UUID(provenance["file_id"])))
            ).scalar_one_or_none()
            if source_file and source_file.collection_id == target.id:
                if source_file.current_version != provenance.get("version"):
                    return {
                        "error": "conflict",
                        "message": (
                            f"'{target_filename}' in '{coll_ref}' is at version "
                            f"{source_file.current_version}, but this file was checked out at "
                            f"version {provenance.get('version')}. Re-run workbench_checkout to "
                            "get the latest, re-apply your changes, then promote again."
                        ),
                    }
                result = await _write_bytes(
                    db,
                    storage,
                    target,
                    filename=source_file.name,
                    content=content,
                    content_type=wb_file.content_type,
                    user_id=user_id,
                    visibility=source_file.visibility,
                    existing=source_file,
                )
                if "error" in result:
                    return result
                # Re-stamp provenance at the new source version so a second
                # promote doesn't see its own update as a conflict.
                new_meta = dict(wb_file.file_metadata or {})
                new_meta[PROVENANCE_KEY] = {**provenance, "version": result["version"]}
                wb_file.file_metadata = new_meta
                await db.commit()  # tool sessions are discarded uncommitted
                return {**result, "collection": coll_ref, "updated_source": True}
            # Source file vanished — fall through to create-new.

        visibility = arguments.get("visibility", "private")
        if visibility not in ("private", "shared"):
            return {"error": "visibility must be 'private' or 'shared'"}
        if visibility == "shared" and not target.allow_shared_files:
            return {"error": f"Collection '{coll_ref}' does not allow shared files"}
        if visibility == "private" and not target.allow_private_files:
            return {"error": f"Collection '{coll_ref}' does not allow private files"}

        result = await _write_bytes(
            db,
            storage,
            target,
            filename=target_filename,
            content=content,
            content_type=wb_file.content_type,
            user_id=user_id,
            visibility=visibility,
        )
        if "error" in result:
            return result
        await db.commit()  # tool sessions are discarded uncommitted
        return {**result, "collection": coll_ref, "updated_source": False}

    # ── helpers ─────────────────────────────────────────────────────

    async def _current_version(self, db: AsyncSession, f: File) -> Optional[FileVersion]:
        return (
            await db.execute(
                select(FileVersion).where(
                    FileVersion.file_id == f.id,
                    FileVersion.version_number == f.current_version,
                )
            )
        ).scalar_one_or_none()

    async def _check_collection_perm(
        self, db: AsyncSession, user_id: str, namespace: str, name: str, write: bool
    ) -> Optional[dict[str, Any]]:
        from app.core.auth import get_user_permissions
        from app.core.permissions import check_permission

        action = "upload" if write else "download"
        perm = f"sinas.collections/{namespace}/{name}.{action}:own"
        user_permissions = await get_user_permissions(db, user_id)
        if not check_permission(user_permissions, perm):
            verb = "write to" if write else "read from"
            return {
                "error": "Permission denied",
                "message": f"You don't have permission to {verb} collection '{namespace}/{name}'.",
            }
        return None



async def run_content_filter(
    db: AsyncSession,
    function_ref: str,
    *,
    namespace: str,
    collection_name: str,
    filename: str,
    content: bytes,
    content_type: str,
    user_id: str,
) -> Optional[dict[str, Any]]:
    """Run a content filter function against candidate bytes; None = approved.

    Same contract as the collection upload endpoint: the filter receives the
    candidate file and must return {"approved": true} for the write to
    proceed. Used by workbench_promote (the target collection's filter) and
    by workbench uploads (the deployment-wide workbench filter setting).
    """
    import base64

    from app.models.execution import TriggerType
    from app.models.function import Function
    from app.services.queue_service import queue_service

    filter_namespace, filter_name = function_ref.split("/")
    func_record = await Function.get_by_name(db, filter_namespace, filter_name)
    if not func_record:
        return {
            "error": (
                f"Content filter function '{function_ref}' "
                f"configured for '{namespace}/{collection_name}' was not found"
            )
        }
    try:
        filter_result = await queue_service.enqueue_and_wait(
            function_namespace=filter_namespace,
            function_name=filter_name,
            input_data={
                "content_base64": base64.b64encode(content).decode(),
                "namespace": namespace,
                "collection": collection_name,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(content),
                "user_metadata": {},
                "user_id": user_id,
            },
            execution_id=str(uuid_lib.uuid4()),
            trigger_type=TriggerType.MANUAL.value,
            trigger_id=f"content_filter:{namespace}/{collection_name}",
            user_id=user_id,
        )
    except Exception as e:
        return {"error": f"Content filter failed to run: {e}"}
    if not (isinstance(filter_result, dict) and filter_result.get("approved")):
        reason = ""
        if isinstance(filter_result, dict):
            reason = filter_result.get("reason") or filter_result.get("message") or ""
        return {
            "error": "Rejected by content filter",
            "message": reason or f"'{namespace}/{collection_name}' declined this file.",
        }
    return None


# ---------------------------------------------------------------------------
# Sandbox sync (eager copy-in / copy-out)
# ---------------------------------------------------------------------------


async def chat_has_workbench_enabled(db: AsyncSession, chat: Chat) -> bool:
    """True when the chat's agent opted in via system_tools: ["workbench"]."""
    from app.models.agent import Agent
    from app.services.system_tool_helpers import has_system_tool

    if not chat or not chat.agent_id:
        return False
    agent = (await db.execute(select(Agent).where(Agent.id == chat.agent_id))).scalar_one_or_none()
    return bool(agent) and has_system_tool(agent.system_tools or [], "workbench")


async def build_sync_manifest(db: AsyncSession, chat: Chat) -> dict[str, Any]:
    """Collect the workbench tree for copy-in to a sandbox execution.

    Returns {"files": [{path, content_b64, sha256}], "skipped": [{path,
    size_bytes, reason}]} bounded by the workbench_sync_* settings. Skipped
    files stay tool-accessible; they just don't materialize in the sandbox.
    """
    import base64

    from app.core.config import settings

    workbench = await get_or_create_workbench(db, chat)
    rows = (
        await db.execute(select(File).where(File.collection_id == workbench.id))
    ).scalars().all()

    storage = get_storage()
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total = 0
    for f in rows:
        version = (
            await db.execute(
                select(FileVersion).where(
                    FileVersion.file_id == f.id,
                    FileVersion.version_number == f.current_version,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            continue
        if version.size_bytes > settings.workbench_sync_max_file_bytes:
            skipped.append({"path": f.name, "size_bytes": version.size_bytes, "reason": "file too large"})
            continue
        if total + version.size_bytes > settings.workbench_sync_max_total_bytes:
            skipped.append({"path": f.name, "size_bytes": version.size_bytes, "reason": "total sync cap reached"})
            continue
        try:
            content = await storage.read(version.storage_path)
        except Exception as e:
            skipped.append({"path": f.name, "size_bytes": version.size_bytes, "reason": f"read failed: {e}"})
            continue
        total += len(content)
        files.append(
            {
                "path": f.name,
                "content_b64": base64.b64encode(content).decode(),
                "sha256": version.hash_sha256,
            }
        )
    return {"files": files, "skipped": skipped}


async def apply_sync_changes(
    db: AsyncSession, chat: Chat, user_id: str, changes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Write files an execution created or modified back into the workbench.

    Each change is {path, content_b64}. Deletions are deliberately NOT
    synced: a crashed or chdir-ing execution must never wipe workbench
    files it simply didn't touch. Returns {"synced": [...], "rejected":
    [{path, reason}]}.
    """
    import base64

    from app.services.collection_tools import _infer_content_type

    workbench = await get_or_create_workbench(db, chat)
    storage = get_storage()
    synced: list[str] = []
    rejected: list[dict[str, str]] = []
    for change in changes or []:
        path = change.get("path", "")
        err = _validate_path(path)
        if err:
            rejected.append({"path": path, "reason": err})
            continue
        try:
            content = base64.b64decode(change.get("content_b64", ""))
        except Exception:
            rejected.append({"path": path, "reason": "invalid base64 content"})
            continue
        result = await _write_bytes(
            db,
            storage,
            workbench,
            filename=path,
            content=content,
            content_type=_infer_content_type(path),
            user_id=user_id,
            visibility="private",
        )
        if "error" in result:
            rejected.append({"path": path, "reason": result["error"]})
        else:
            synced.append(path)
    return {"synced": synced, "rejected": rejected}


async def _write_bytes(
    db: AsyncSession,
    storage: FileStorage,
    collection: Collection,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    user_id: str,
    visibility: str,
    file_metadata: Optional[dict[str, Any]] = None,
    existing: Optional[File] = None,
) -> dict[str, Any]:
    """Byte-level write into a collection/workbench (checkout + promote path).

    Mirrors CollectionToolConverter._write_file but takes bytes (binary-safe)
    and lets the caller pin metadata, visibility, and the target File row.
    Does NOT run content filters or permission checks — callers do.
    """
    size_mb = len(content) / (1024 * 1024)
    if size_mb > collection.max_file_size_mb:
        return {"error": f"File too large ({size_mb:.2f}MB > {collection.max_file_size_mb}MB limit)"}

    file_hash = storage.calculate_hash(content)

    if existing is None:
        existing = (
            await db.execute(
                select(File)
                .where(and_(File.collection_id == collection.id, File.name == filename))
                .where(or_(File.user_id == uuid_lib.UUID(user_id), File.visibility == "shared"))
                .order_by((File.user_id == uuid_lib.UUID(user_id)).desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if existing:
        existing.current_version += 1
        existing.content_type = content_type
        if file_metadata is not None:
            existing.file_metadata = file_metadata
        file_record = existing
        created = False
    else:
        file_record = File(
            collection_id=collection.id,
            name=filename,
            user_id=uuid_lib.UUID(user_id),
            content_type=content_type,
            current_version=1,
            file_metadata=file_metadata or {},
            visibility=visibility,
        )
        db.add(file_record)
        created = True

    await db.flush()

    storage_path = f"{collection.namespace}/{collection.name}/{file_record.id}/v{file_record.current_version}"
    try:
        await storage.save(storage_path, content)
    except Exception as e:
        return {"error": f"Storage write failed: {e}"}

    db.add(
        FileVersion(
            file_id=file_record.id,
            version_number=file_record.current_version,
            storage_path=storage_path,
            size_bytes=len(content),
            hash_sha256=file_hash,
            uploaded_by=uuid_lib.UUID(user_id),
        )
    )
    await db.flush()

    return {
        "filename": file_record.name,
        "version": file_record.current_version,
        "size_bytes": len(content),
        "created": created,
    }
