"""Collection-to-tool converter for LLM tool calling."""
import base64
import json
import logging
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import Collection, File, FileVersion
from app.services.file_storage import FileStorage, generate_file_data_url, generate_file_url, get_storage

logger = logging.getLogger(__name__)

# Content types treated as inline text
TEXT_CONTENT_TYPES = {
    "text/plain",
    "text/html",
    "text/css",
    "text/javascript",
    "text/csv",
    "text/markdown",
    "text/xml",
    "text/yaml",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
    "application/typescript",
    "application/x-python",
    "application/x-sh",
    "application/sql",
}


def _is_text_content(content_type: str) -> bool:
    """Check if a content type should be returned as inline text."""
    if content_type in TEXT_CONTENT_TYPES:
        return True
    if content_type.startswith("text/"):
        return True
    return False


def _flatten_metadata(metadata: dict) -> list[str]:
    """Recursively flatten metadata dict values into a list of strings."""
    values = []
    for v in metadata.values():
        if isinstance(v, dict):
            values.extend(_flatten_metadata(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    values.extend(_flatten_metadata(item))
                else:
                    values.append(str(item))
        else:
            values.append(str(v))
    return values


def _safe_tool_name(prefix: str, namespace: str, name: str) -> str:
    """Create a safe function name from prefix + namespace/name."""
    safe = f"{prefix}_{namespace}_{name}".replace("-", "_").replace(" ", "_")
    return safe


class CollectionToolConverter:
    """Converts collections to OpenAI tool format and handles execution."""

    async def get_available_collections(
        self,
        db: AsyncSession,
        user_id: str,
        enabled_collections: list,
    ) -> list[dict[str, Any]]:
        """
        Get collection tools in OpenAI format.

        Each enabled collection produces read tools (always):
        - search_collection_{ns}_{name}: Search files by metadata/query
        - get_file_{ns}_{name}: Get file content (text inline, binary as URL)

        Plus write tools when access is "readwrite":
        - write_file_{ns}_{name}: Write/overwrite a file (creates new version)
        - edit_file_{ns}_{name}: Surgical edit via exact string replacement
        - delete_file_{ns}_{name}: Delete a file from the collection
        """
        tools = []

        for entry in enabled_collections:
            # Support both legacy strings and new dict format
            if isinstance(entry, str):
                coll_ref = entry
                access = "readonly"
            elif isinstance(entry, dict):
                coll_ref = entry.get("collection", "")
                access = entry.get("access", "readonly")
            else:
                logger.warning(f"Invalid collection entry: {entry}")
                continue

            if "/" not in coll_ref:
                logger.warning(f"Invalid collection reference format: {coll_ref}")
                continue

            namespace, name = coll_ref.split("/", 1)
            collection = await Collection.get_by_name(db, namespace, name)
            if not collection:
                logger.warning(f"Collection {coll_ref} not found")
                continue

            is_readwrite = access == "readwrite"

            # Search tool (always)
            search_name = _safe_tool_name("search_collection", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": search_name,
                    "description": f"Search files in the '{namespace}/{name}' collection by name, metadata, or content. Returns matching filenames, content types, versions, and metadata. Use get_file to retrieve the actual content or a shareable URL for a specific file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Optional text/regex query to search file contents",
                            },
                            "metadata_filter": {
                                "type": "object",
                                "description": "Optional key-value pairs to filter files by metadata",
                            },
                        },
                        "required": [],
                    },
                    "_metadata": {
                        "collection_ref": coll_ref,
                        "tool_type": "collection_search",
                    },
                },
            })

            # Get file tool (always)
            get_name = _safe_tool_name("get_file", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": get_name,
                    "description": (
                        f"Get a file from the '{namespace}/{name}' collection. For text files, returns content inline "
                        "(with line numbers). For images and other binary files, returns a temporary public URL. "
                        "Use offset and limit to read specific line ranges of large files."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "The filename to retrieve",
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Start line (1-indexed). Omit to read from the beginning.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of lines to return. Omit to read the entire file.",
                            },
                        },
                        "required": ["filename"],
                    },
                    "_metadata": {
                        "collection_ref": coll_ref,
                        "tool_type": "collection_get_file",
                    },
                },
            })

            if not is_readwrite:
                continue

            # Write file tool (readwrite only)
            write_name = _safe_tool_name("write_file", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": write_name,
                    "description": (
                        f"Write content to a file in the '{namespace}/{name}' collection. "
                        "If the file already exists, a new version is created (existing "
                        "content is overwritten). For targeted changes to an existing file, "
                        "use edit_file instead. Returns the new version number."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Filename to write (e.g. 'config.yaml', 'notes.md')",
                            },
                            "content": {
                                "type": "string",
                                "description": "Full file contents to write.",
                            },
                        },
                        "required": ["filename", "content"],
                    },
                    "_metadata": {
                        "collection_ref": coll_ref,
                        "tool_type": "collection_write_file",
                    },
                },
            })

            # Edit file tool (readwrite only)
            edit_name = _safe_tool_name("edit_file", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": edit_name,
                    "description": (
                        f"Make a targeted edit to an existing file in the '{namespace}/{name}' "
                        "collection by replacing an exact string. old_string must appear "
                        "EXACTLY ONCE in the file, otherwise the edit fails (unless "
                        "replace_all is true). Use get_file first to read the current "
                        "contents. Creates a new file version."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Filename to edit",
                            },
                            "old_string": {
                                "type": "string",
                                "description": "Exact string to find. Must be unique in the file.",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Replacement string.",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "Replace every occurrence instead of requiring uniqueness. Default false.",
                            },
                        },
                        "required": ["filename", "old_string", "new_string"],
                    },
                    "_metadata": {
                        "collection_ref": coll_ref,
                        "tool_type": "collection_edit_file",
                    },
                },
            })

            # Delete file tool (readwrite only)
            delete_name = _safe_tool_name("delete_file", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": delete_name,
                    "description": f"Delete a file from the '{namespace}/{name}' collection. This is permanent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "Filename to delete",
                            },
                        },
                        "required": ["filename"],
                    },
                    "_metadata": {
                        "collection_ref": coll_ref,
                        "tool_type": "collection_delete_file",
                    },
                },
            })

        return tools

    async def execute_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a collection tool call.

        Args:
            db: Database session
            tool_name: The tool name (search_collection_* or get_file_*)
            arguments: Tool arguments from the LLM
            user_id: Current user ID
            metadata: Tool metadata containing collection_ref and tool_type
        """
        from app.core.auth import get_user_permissions
        from app.core.permissions import check_permission

        coll_ref = metadata.get("collection_ref", "")
        tool_type = metadata.get("tool_type", "")

        if "/" not in coll_ref:
            return {"error": f"Invalid collection reference: {coll_ref}"}

        namespace, name = coll_ref.split("/", 1)
        collection = await Collection.get_by_name(db, namespace, name)
        if not collection:
            return {"error": f"Collection '{coll_ref}' not found"}

        # Permission check: read operations need download, write operations need upload
        user_permissions = await get_user_permissions(db, user_id)
        is_write = tool_type in ("collection_write_file", "collection_edit_file", "collection_delete_file")
        if is_write:
            perm = f"sinas.collections/{namespace}/{name}.upload:own"
        else:
            perm = f"sinas.collections/{namespace}/{name}.download:own"
        if not check_permission(user_permissions, perm):
            return {
                "error": "Permission denied",
                "message": f"You don't have permission to {'write to' if is_write else 'read from'} collection '{coll_ref}'.",
            }

        if tool_type == "collection_search":
            return await self._search_collection(db, collection, namespace, user_id, arguments)
        elif tool_type == "collection_get_file":
            return await self._get_file(db, collection, namespace, user_id, arguments)
        elif tool_type == "collection_write_file":
            return await self._write_file(db, collection, namespace, user_id, arguments)
        elif tool_type == "collection_edit_file":
            return await self._edit_file(db, collection, namespace, user_id, arguments)
        elif tool_type == "collection_delete_file":
            return await self._delete_file(db, collection, namespace, user_id, arguments)
        else:
            return {"error": f"Unknown collection tool type: {tool_type}"}

    async def _search_collection(
        self,
        db: AsyncSession,
        collection: Collection,
        namespace: str,
        user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Search files in a collection."""
        query = select(File).where(File.collection_id == collection.id)

        # Visibility: show shared files + user's own private files
        query = query.where(
            or_(
                File.user_id == user_id,
                File.visibility == "shared",
            )
        )

        # Apply metadata filters
        metadata_filter = arguments.get("metadata_filter")
        if metadata_filter:
            for key, value in metadata_filter.items():
                query = query.where(File.file_metadata[key].as_string() == str(value))

        query = query.order_by(File.name).limit(50)
        result = await db.execute(query)
        files = result.scalars().all()

        # If text query provided, filter by filename, metadata, and content
        text_query = arguments.get("query")
        if text_query:
            import re as re_module

            # Split query into search terms for flexible matching
            # "Conformity logo" -> ["conformity", "logo"]
            terms = [t.lower() for t in text_query.split() if t.strip()]

            # Also try the full query as a regex for content search
            try:
                content_pattern = re_module.compile(text_query, re_module.IGNORECASE)
            except re_module.error:
                content_pattern = re_module.compile(re_module.escape(text_query), re_module.IGNORECASE)

            storage = get_storage()
            matching_files = []

            for file_record in files:
                # 1. Match filename: all terms must appear (ignoring separators)
                # Normalize filename: "conformity-logo.png" -> "conformity logo png"
                normalized_name = re_module.sub(r"[_\-./\\]", " ", file_record.name).lower()
                name_match = any(term in normalized_name for term in terms)

                # 2. Match metadata values: flatten all values to a searchable string
                meta_str = " ".join(
                    str(v).lower() for v in _flatten_metadata(file_record.file_metadata)
                )
                meta_match = any(term in meta_str for term in terms)

                if name_match or meta_match:
                    matching_files.append(file_record)
                    continue

                # 3. For text files, also search content with regex
                if _is_text_content(file_record.content_type):
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
                        continue

                    try:
                        content = await storage.read(file_version.storage_path)
                        text = content.decode("utf-8")
                        if content_pattern.search(text):
                            matching_files.append(file_record)
                    except Exception:
                        continue

            files = matching_files

        results = []
        for f in files:
            results.append({
                "filename": f.name,
                "content_type": f.content_type,
                "version": f.current_version,
                "metadata": f.file_metadata,
                "visibility": f.visibility,
            })

        return {"files": results, "count": len(results)}

    async def _get_file(
        self,
        db: AsyncSession,
        collection: Collection,
        namespace: str,
        user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Get a file's content or URL."""
        filename = arguments.get("filename")
        if not filename:
            return {"error": "filename is required"}

        # Find the file
        result = await db.execute(
            select(File).where(
                and_(
                    File.collection_id == collection.id,
                    File.name == filename,
                )
            )
        )
        file_record = result.scalar_one_or_none()
        if not file_record:
            return {"error": f"File '{filename}' not found in collection"}

        # Check visibility
        if file_record.visibility == "private" and str(file_record.user_id) != user_id:
            return {"error": f"File '{filename}' is not accessible"}

        # Get current version
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
            return {"error": f"Version not found for file '{filename}'"}

        base_info = {
            "filename": file_record.name,
            "content_type": file_record.content_type,
            "version": file_record.current_version,
            "metadata": file_record.file_metadata,
            "size_bytes": file_version.size_bytes,
        }

        # Text files: return content inline (with optional line range)
        if _is_text_content(file_record.content_type):
            storage = get_storage()
            try:
                content = await storage.read(file_version.storage_path)
                text = content.decode("utf-8")
            except Exception as e:
                return {**base_info, "error": f"Failed to read file: {str(e)}"}

            # Internal callers (e.g. _edit_file) pass _raw=True to skip line numbers
            raw_mode = arguments.get("_raw", False)

            lines = text.split("\n")
            total_lines = len(lines)
            offset = arguments.get("offset")
            limit = arguments.get("limit")

            if offset is not None or limit is not None:
                start = max(0, (offset or 1) - 1)  # 1-indexed → 0-indexed
                end = start + limit if limit is not None else total_lines
                sliced = lines[start:end]
                if raw_mode:
                    return {**base_info, "content": "\n".join(sliced), "total_lines": total_lines}
                numbered = "\n".join(
                    f"{start + i + 1:>6}\t{line}" for i, line in enumerate(sliced)
                )
                return {
                    **base_info,
                    "content": numbered,
                    "line_range": [start + 1, start + len(sliced)],
                    "total_lines": total_lines,
                }

            if raw_mode:
                return {**base_info, "content": text, "total_lines": total_lines}

            numbered = "\n".join(f"{i + 1:>6}\t{line}" for i, line in enumerate(lines))
            return {**base_info, "content": numbered, "total_lines": total_lines}

        # Binary/image files: return URL or data URL
        url = generate_file_url(str(file_record.id), file_record.current_version)
        if url:
            return {**base_info, "url": url}

        # Fallback to data URL for localhost
        try:
            data_url = await generate_file_data_url(file_version.storage_path, file_record.content_type)
            return {**base_info, "url": data_url}
        except Exception as e:
            return {**base_info, "error": f"Failed to generate file URL: {str(e)}"}

    # ── Write / Edit / Delete (readwrite collections only) ──────────

    async def _write_file(
        self,
        db: AsyncSession,
        collection: Collection,
        namespace: str,
        user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Write content to a file, creating a new version if it exists."""
        import uuid as uuid_lib
        filename = arguments.get("filename")
        content = arguments.get("content")
        if not filename:
            return {"error": "filename is required"}
        if content is None:
            return {"error": "content is required"}

        storage: FileStorage = get_storage()
        content_bytes = content.encode("utf-8")
        file_hash = storage.calculate_hash(content_bytes)

        # Check size limits
        file_size_mb = len(content_bytes) / (1024 * 1024)
        if file_size_mb > collection.max_file_size_mb:
            return {"error": f"File too large ({file_size_mb:.2f}MB > {collection.max_file_size_mb}MB limit)"}

        # Infer content type from extension
        content_type = _infer_content_type(filename)

        # Find existing file
        result = await db.execute(
            select(File).where(
                and_(File.collection_id == collection.id, File.name == filename)
            ).where(
                or_(File.user_id == user_id, File.visibility == "shared")
            ).order_by((File.user_id == user_id).desc()).limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.current_version += 1
            existing.content_type = content_type
            file_record = existing
            created = False
        else:
            file_record = File(
                collection_id=collection.id,
                name=filename,
                user_id=uuid_lib.UUID(user_id),
                content_type=content_type,
                current_version=1,
                file_metadata={},
                visibility="shared",
            )
            db.add(file_record)
            created = True

        await db.flush()

        storage_path = f"{namespace}/{collection.name}/{file_record.id}/v{file_record.current_version}"

        try:
            await storage.save(storage_path, content_bytes)
        except Exception as e:
            await db.rollback()
            return {"error": f"Storage write failed: {str(e)}"}

        version = FileVersion(
            file_id=file_record.id,
            version_number=file_record.current_version,
            storage_path=storage_path,
            size_bytes=len(content_bytes),
            hash_sha256=file_hash,
            uploaded_by=uuid_lib.UUID(user_id),
        )
        db.add(version)

        try:
            await db.commit()
            await db.refresh(file_record)
        except Exception as e:
            try:
                await storage.delete(storage_path)
            except Exception:
                pass
            return {"error": f"DB commit failed: {str(e)}"}

        return {
            "filename": file_record.name,
            "version": file_record.current_version,
            "size_bytes": len(content_bytes),
            "created": created,
        }

    async def _edit_file(
        self,
        db: AsyncSession,
        collection: Collection,
        namespace: str,
        user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Surgical edit: exact string replacement in a file. Creates new version."""
        filename = arguments.get("filename")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all", False))

        if not filename:
            return {"error": "filename is required"}
        if not old_string:
            return {"error": "old_string is required and must be non-empty"}

        # Read current content (raw, without line numbers)
        get_result = await self._get_file(db, collection, namespace, user_id, {"filename": filename, "_raw": True})
        if "error" in get_result:
            return get_result
        if "content" not in get_result:
            return {"error": "Cannot edit binary files — file must be text"}

        current = get_result["content"]
        occurrences = current.count(old_string)

        if occurrences == 0:
            return {"error": f"old_string not found in '{filename}'"}
        if occurrences > 1 and not replace_all:
            return {
                "error": (
                    f"old_string appears {occurrences} times in '{filename}'. "
                    "Provide more context to make it unique, or set replace_all=true."
                )
            }

        new_content = current.replace(old_string, new_string) if replace_all else current.replace(old_string, new_string, 1)

        # Write back as new version
        return await self._write_file(db, collection, namespace, user_id, {
            "filename": filename,
            "content": new_content,
        })

    async def _delete_file(
        self,
        db: AsyncSession,
        collection: Collection,
        namespace: str,
        user_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Delete a file from the collection."""
        filename = arguments.get("filename")
        if not filename:
            return {"error": "filename is required"}

        result = await db.execute(
            select(File).where(
                and_(File.collection_id == collection.id, File.name == filename)
            ).where(
                or_(File.user_id == user_id, File.visibility == "shared")
            ).limit(1)
        )
        file_record = result.scalar_one_or_none()
        if not file_record:
            return {"error": f"File '{filename}' not found in collection"}

        # Delete all versions from storage
        storage: FileStorage = get_storage()
        ver_result = await db.execute(
            select(FileVersion).where(FileVersion.file_id == file_record.id)
        )
        for ver in ver_result.scalars().all():
            try:
                await storage.delete(ver.storage_path)
            except Exception:
                pass

        await db.delete(file_record)
        await db.commit()

        return {"filename": filename, "deleted": True}


# ── Helpers ──────────────────────────────────────────────────

_CONTENT_TYPE_BY_EXT: dict[str, str] = {
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".json": "application/json",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".txt": "text/plain",
    ".sql": "application/sql",
    ".js": "application/javascript",
    ".ts": "application/typescript",
    ".csv": "text/csv",
    ".html": "text/html",
    ".css": "text/css",
    ".xml": "application/xml",
}


def _infer_content_type(filename: str) -> str:
    """Guess MIME type from filename extension; default text/plain."""
    lower = filename.lower()
    for ext, ct in _CONTENT_TYPE_BY_EXT.items():
        if lower.endswith(ext):
            return ct
    return "text/plain"
