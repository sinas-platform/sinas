"""Collection-to-tool converter for LLM tool calling."""
import json
import logging
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import Collection, File, FileVersion
from app.services.file_storage import generate_file_data_url, generate_file_url, get_storage

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
        enabled_collections: list[str],
    ) -> list[dict[str, Any]]:
        """
        Get collection tools in OpenAI format.

        Each enabled collection produces two tools:
        - search_collection_{ns}_{name}: Search files by metadata/query
        - get_file_{ns}_{name}: Get file content (text inline, binary as URL)
        """
        tools = []

        for coll_ref in enabled_collections:
            if "/" not in coll_ref:
                logger.warning(f"Invalid collection reference format: {coll_ref}")
                continue

            namespace, name = coll_ref.split("/", 1)
            collection = await Collection.get_by_name(db, namespace, name)
            if not collection:
                logger.warning(f"Collection {coll_ref} not found")
                continue

            # Search tool
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

            # Get file tool
            get_name = _safe_tool_name("get_file", namespace, name)
            tools.append({
                "type": "function",
                "function": {
                    "name": get_name,
                    "description": f"Get a file from the '{namespace}/{name}' collection. For text files, returns content inline. For images and other binary files, returns a temporary public URL that can be shared. Always use this tool to get file URLs — never construct URLs yourself.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": "The filename to retrieve",
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
        coll_ref = metadata.get("collection_ref", "")
        tool_type = metadata.get("tool_type", "")

        if "/" not in coll_ref:
            return {"error": f"Invalid collection reference: {coll_ref}"}

        namespace, name = coll_ref.split("/", 1)
        collection = await Collection.get_by_name(db, namespace, name)
        if not collection:
            return {"error": f"Collection '{coll_ref}' not found"}

        if tool_type == "collection_search":
            return await self._search_collection(db, collection, namespace, user_id, arguments)
        elif tool_type == "collection_get_file":
            return await self._get_file(db, collection, namespace, user_id, arguments)
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

        # Text files: return content inline
        if _is_text_content(file_record.content_type):
            storage = get_storage()
            try:
                content = await storage.read(file_version.storage_path)
                text = content.decode("utf-8")
                return {**base_info, "content": text}
            except Exception as e:
                return {**base_info, "error": f"Failed to read file: {str(e)}"}

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
