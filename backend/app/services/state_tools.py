"""State store tools for LLM to save/retrieve state within stores."""
import json
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

import jsonschema
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.encryption import encryption_service
from app.models.state import State
from app.models.store import Store


def _decrypt_state_value(state: State) -> Any:
    """Return the decrypted value for a state, handling both encrypted and plain states."""
    if state.encrypted and state.encrypted_value:
        return json.loads(encryption_service.decrypt(state.encrypted_value))
    return state.value


class StateTools:
    """Provides LLM tools for interacting with state stores."""

    @staticmethod
    async def get_tool_definitions(
        db: Optional[AsyncSession] = None,
        user_id: Optional[str] = None,
        enabled_stores: Optional[list[dict[str, str]]] = None,
    ) -> list[dict[str, Any]]:
        """
        Get OpenAI-compatible tool definitions for state operations.

        Args:
            db: Optional database session for enriching tool descriptions
            user_id: Optional user ID for personalizing tool descriptions
            enabled_stores: List of {"store": "namespace/name", "access": "readonly|readwrite"}
        """
        if not enabled_stores:
            return []

        readonly_stores = [s["store"] for s in enabled_stores if s.get("access") == "readonly"]
        readwrite_stores = [s["store"] for s in enabled_stores if s.get("access") == "readwrite"]
        all_stores = readonly_stores + readwrite_stores

        if not all_stores:
            return []

        # Get available keys info
        available_keys_info = ""
        if db and user_id:
            available_keys_info = await StateTools._get_available_keys_description(
                db, user_id, allowed_stores=all_stores
            )

        # Load store objects for schema/strict info
        store_objects: dict[str, Store] = {}
        if db:
            for store_ref in all_stores:
                store_obj = await StateTools._resolve_store(db, store_ref)
                if store_obj:
                    store_objects[store_ref] = store_obj

        # Build store info
        store_info = ""
        if readwrite_stores:
            rw_list = ", ".join([f"'{s}'" for s in readwrite_stores])
            store_info += f"\n\nRead-write stores: {rw_list}. You can save/update/delete state in these stores."
        if readonly_stores:
            ro_list = ", ".join([f"'{s}'" for s in readonly_stores])
            store_info += f"\n\nRead-only stores: {ro_list}. You can only retrieve state from these stores."

        # Add schema info per store
        for ref, store_obj in store_objects.items():
            if store_obj.description:
                store_info += f"\n\nStore '{ref}': {store_obj.description}"
            if store_obj.schema and store_obj.schema.get("properties"):
                keys_desc = []
                for key, prop_schema in store_obj.schema["properties"].items():
                    prop_type = prop_schema.get("type", "any")
                    prop_desc = prop_schema.get("description", "")
                    entry = f"'{key}' ({prop_type})"
                    if prop_desc:
                        entry += f" - {prop_desc}"
                    keys_desc.append(entry)
                mode = "strict (values must match schema)" if store_obj.strict else "non-strict (schema is advisory)"
                store_info += f"\n\nStore '{ref}' schema ({mode}): {', '.join(keys_desc)}"

        save_description = (
            "Save information to a state store for future recall. Use this to remember "
            "user preferences, facts learned during conversation, important decisions, "
            "or any information that should persist across conversations."
        )
        if store_info:
            save_description += store_info

        retrieve_description = (
            "Retrieve saved state by store and key. Use this to recall "
            "previously saved information, preferences, or facts. "
            "Use list_state_keys first to discover what's available."
        )
        if store_info:
            retrieve_description += store_info

        tools = []

        # list_state_keys — introspection tool
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "list_state_keys",
                    "description": (
                        "Discover what's stored in state stores. Without parameters, "
                        "returns a summary: key count and tag distribution per store. "
                        "With a store specified, lists keys (with descriptions and tags). "
                        "Filter by tags to narrow results. Always call this before "
                        "retrieve_state to know what keys exist."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "store": {
                                "type": "string",
                                "description": "Store to list keys from. Omit for an overview of all stores.",
                                "enum": all_stores,
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Filter keys by tags (all must match).",
                            },
                            "search": {
                                "type": "string",
                                "description": "Search term to match in key names and descriptions.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max keys to return (default 50).",
                            },
                        },
                    },
                },
            }
        )

        # retrieve_state for getting actual values
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "retrieve_state",
                    "description": retrieve_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "store": {
                                "type": "string",
                                "description": "Store reference (e.g., 'default/memory')",
                                "enum": all_stores,
                            },
                            "key": {
                                "type": "string",
                                "description": "Specific key to retrieve.",
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Filter by tags (returns all matching keys).",
                            },
                            "search": {
                                "type": "string",
                                "description": "Search term to find in keys and descriptions.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return.",
                                "default": 10,
                            },
                        },
                    },
                },
            }
        )

        # Write tools only for readwrite stores
        if readwrite_stores:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "save_state",
                        "description": save_description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "store": {
                                    "type": "string",
                                    "description": "Store to save in",
                                    "enum": readwrite_stores,
                                },
                                "key": {
                                    "type": "string",
                                    "description": "Unique identifier within the store. Creates new or updates existing.",
                                },
                                "value": {
                                    "description": "Data to store (any valid JSON value)",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Human-readable description of what this state contains",
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Tags for categorization and search",
                                },
                                "visibility": {
                                    "type": "string",
                                    "enum": ["private", "shared"],
                                    "description": "Who can access: 'private' (user only) or 'shared' (permitted users)",
                                    "default": "private",
                                },
                            },
                            "required": ["store", "key", "value"],
                        },
                    },
                }
            )

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "delete_state",
                        "description": "Delete a state entry when it's no longer needed or is outdated.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "store": {
                                    "type": "string",
                                    "description": "Store containing the state",
                                    "enum": readwrite_stores,
                                },
                                "key": {
                                    "type": "string",
                                    "description": "Key of the state to delete",
                                },
                            },
                            "required": ["store", "key"],
                        },
                    },
                }
            )

        return tools

    @staticmethod
    async def _resolve_store(db: AsyncSession, store_ref: str) -> Optional[Store]:
        """Resolve a store reference like 'namespace/name' to a Store object."""
        parts = store_ref.split("/", 1)
        if len(parts) != 2:
            return None
        return await Store.get_by_name(db, parts[0], parts[1])

    @staticmethod
    async def _get_available_keys_description(
        db: AsyncSession,
        user_id: str,
        allowed_stores: Optional[list[str]] = None,
    ) -> str:
        """Get a summary of available state keys for this user."""
        user_uuid = uuid_lib.UUID(user_id)

        if not allowed_stores:
            return ""

        # Resolve store refs to IDs
        store_ids = {}
        for store_ref in allowed_stores:
            store = await StateTools._resolve_store(db, store_ref)
            if store:
                store_ids[store.id] = store_ref

        if not store_ids:
            return ""

        # Own states and shared states in allowed stores
        visibility_filter = or_(
            State.user_id == user_uuid,
            and_(State.visibility == "shared", State.store_id.in_(store_ids.keys())),
        )

        query = (
            select(State.store_id, State.key, State.description)
            .where(
                and_(
                    State.store_id.in_(store_ids.keys()),
                    or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                    visibility_filter,
                )
            )
            .order_by(State.store_id, State.key)
        )

        result = await db.execute(query)
        contexts = result.all()

        if not contexts:
            return ""

        # Group by store
        by_store: dict[str, list[tuple]] = {}
        for store_id, key, description in contexts:
            store_ref = store_ids.get(store_id, str(store_id))
            if store_ref not in by_store:
                by_store[store_ref] = []
            by_store[store_ref].append((key, description))

        lines = ["Currently available state:"]
        for store_ref in sorted(by_store.keys()):
            keys = by_store[store_ref]
            key_list = ", ".join([f"'{key}'" for key, _ in keys])
            lines.append(f"  • {store_ref}: {key_list}")

        return "\n".join(lines)

    @staticmethod
    async def execute_tool(
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        chat_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a state tool.

        Access control is layered:
        1. Agent's enabled_stores gates which stores are available as tools
        2. User's RBAC permissions gate actual access at execution time
        """
        from app.core.auth import get_user_permissions
        from app.core.permissions import check_permission

        # Get agent's enabled_stores for validation
        write_stores = None
        all_allowed_stores = None
        if agent_id:
            from app.models.agent import Agent

            result = await db.execute(select(Agent).where(Agent.id == uuid_lib.UUID(agent_id)))
            agent = result.scalar_one_or_none()
            if agent and agent.enabled_stores:
                write_stores = [s["store"] for s in agent.enabled_stores if s.get("access") == "readwrite"]
                all_allowed_stores = [s["store"] for s in agent.enabled_stores]

        # Check agent's enabled_stores for write operations
        if tool_name in ("save_state", "update_state", "delete_state") and write_stores is not None:
            requested_store = arguments.get("store")
            if not requested_store or requested_store not in write_stores:
                return {
                    "error": f"Agent not authorized to write to store '{requested_store}'",
                    "allowed_stores": write_stores,
                }

        # Check user's RBAC permissions for store access
        requested_store = arguments.get("store")
        if requested_store and "/" in requested_store:
            ns, name = requested_store.split("/", 1)
            user_permissions = await get_user_permissions(db, user_id)
            is_write = tool_name in ("save_state", "update_state", "delete_state")
            perm = f"sinas.stores/{ns}/{name}.write_state:own" if is_write else f"sinas.stores/{ns}/{name}.read_state:own"
            if not check_permission(user_permissions, perm):
                return {
                    "error": "Permission denied",
                    "message": f"You don't have permission to {'write to' if is_write else 'read from'} store '{requested_store}'.",
                }

        # Map old tool names for backward compatibility
        tool_map = {
            "retrieve_context": "retrieve_state",
            "save_context": "save_state",
            "update_context": "save_state",
            "update_state": "save_state",
            "delete_context": "delete_state",
        }
        tool_name = tool_map.get(tool_name, tool_name)

        if tool_name == "list_state_keys":
            return await StateTools._list_state_keys(db, user_id, arguments, allowed_stores=all_allowed_stores)
        elif tool_name == "save_state":
            return await StateTools._save_state(db, user_id, arguments)
        elif tool_name == "retrieve_state":
            return await StateTools._retrieve_state(db, user_id, arguments, allowed_stores=all_allowed_stores)
        elif tool_name == "delete_state":
            return await StateTools._delete_state(db, user_id, arguments)
        else:
            return {"error": f"Unknown state tool: {tool_name}"}

    @staticmethod
    async def _list_state_keys(
        db: AsyncSession,
        user_id: str,
        arguments: dict[str, Any],
        allowed_stores: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """List state keys with tag distribution. Two modes:

        1. No store specified → overview: per-store key count + top tags
        2. Store specified → list keys with descriptions, tags. Filterable.
        """
        from sqlalchemy import func as sqlfunc

        user_uuid = uuid_lib.UUID(user_id)
        store_filter = arguments.get("store")
        tag_filter = arguments.get("tags", [])
        search = arguments.get("search")
        limit = min(int(arguments.get("limit", 50)), 200)

        if not allowed_stores:
            return {"error": "No stores enabled for this agent"}

        # Resolve store refs to IDs
        store_ids: dict[Any, str] = {}
        for store_ref in allowed_stores:
            store = await StateTools._resolve_store(db, store_ref)
            if store:
                store_ids[store.id] = store_ref

        if not store_ids:
            return {"stores": [], "total_keys": 0}

        # Base filter: user's own + shared, not expired, in allowed stores
        base_filter = and_(
            State.store_id.in_(store_ids.keys()),
            or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
            or_(
                State.user_id == user_uuid,
                and_(State.visibility == "shared", State.store_id.in_(store_ids.keys())),
            ),
        )

        if not store_filter:
            # Overview mode: count keys and aggregate tags per store
            query = (
                select(
                    State.store_id,
                    sqlfunc.count(State.id).label("key_count"),
                )
                .where(base_filter)
                .group_by(State.store_id)
            )
            result = await db.execute(query)
            counts = result.all()

            stores = []
            total = 0
            for store_id, key_count in counts:
                store_ref = store_ids.get(store_id, str(store_id))
                total += key_count

                # Get tag counts for this store
                tag_query = (
                    select(State.tags)
                    .where(and_(base_filter, State.store_id == store_id))
                )
                tag_result = await db.execute(tag_query)
                tag_counts: dict[str, int] = {}
                for (tags,) in tag_result.all():
                    for tag in (tags or []):
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1

                store_info: dict[str, Any] = {
                    "store": store_ref,
                    "key_count": key_count,
                }
                if tag_counts:
                    store_info["tags"] = dict(sorted(tag_counts.items(), key=lambda x: -x[1]))
                stores.append(store_info)

            return {"stores": stores, "total_keys": total}

        # Detail mode: list keys for a specific store
        if store_filter not in allowed_stores:
            return {"error": f"Store '{store_filter}' not in allowed stores"}

        store = await StateTools._resolve_store(db, store_filter)
        if not store:
            return {"error": f"Store '{store_filter}' not found"}

        query = (
            select(State.key, State.description, State.tags, State.updated_at)
            .where(and_(base_filter, State.store_id == store.id))
        )

        # Tag filter: all specified tags must be present
        if tag_filter:
            for tag in tag_filter:
                query = query.where(State.tags.op("@>")(json.dumps([tag])))

        # Search filter
        if search:
            search_pattern = f"%{search}%"
            query = query.where(
                or_(
                    State.key.ilike(search_pattern),
                    State.description.ilike(search_pattern),
                )
            )

        query = query.order_by(State.key).limit(limit)
        result = await db.execute(query)
        rows = result.all()

        keys = []
        for key, description, tags, updated_at in rows:
            entry: dict[str, Any] = {"key": key}
            if description:
                entry["description"] = description
            if tags:
                entry["tags"] = tags
            if updated_at:
                entry["updated_at"] = updated_at.isoformat()
            keys.append(entry)

        return {"store": store_filter, "keys": keys, "count": len(keys)}

    @staticmethod
    async def _save_state(
        db: AsyncSession,
        user_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Save state to store."""
        user_uuid = uuid_lib.UUID(user_id)
        store_ref = args.get("store", "")

        store = await StateTools._resolve_store(db, store_ref)
        if not store:
            return {"error": f"Store '{store_ref}' not found"}

        # Schema validation for strict stores
        if store.strict and store.schema:
            schema = store.schema
            if schema.get("properties") and args["key"] not in schema.get("properties", {}):
                return {
                    "error": f"Key '{args['key']}' is not allowed in strict store '{store_ref}'. "
                             f"Allowed keys: {list(schema.get('properties', {}).keys())}",
                }
            prop_schema = schema.get("properties", {}).get(args["key"])
            if prop_schema:
                try:
                    jsonschema.validate(instance=args["value"], schema=prop_schema)
                except jsonschema.ValidationError as e:
                    return {"error": f"Value validation failed for key '{args['key']}': {e.message}"}

        # Upsert: check if already exists
        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.store_id == store.id,
                    State.key == args["key"],
                )
            )
        )
        existing = result.scalar_one_or_none()

        should_encrypt = store.encrypted
        value = args["value"]
        encrypted_value = None
        if should_encrypt:
            encrypted_value = encryption_service.encrypt(json.dumps(value))
            value = {}

        if existing:
            # Update existing state
            if should_encrypt:
                existing.encrypted_value = encrypted_value
                existing.value = {}
            else:
                existing.value = value
            if "description" in args:
                existing.description = args["description"]
            if "tags" in args:
                existing.tags = args["tags"]
            if "visibility" in args:
                existing.visibility = args["visibility"]

            await db.commit()
            await db.refresh(existing)

            return {
                "success": True,
                "message": f"Updated state: {store_ref}/{args['key']}",
                "state_id": str(existing.id),
                "value": _decrypt_state_value(existing),
            }

        visibility = args.get("visibility", store.default_visibility)

        state = State(
            user_id=user_uuid,
            store_id=store.id,
            key=args["key"],
            value=value,
            encrypted=should_encrypt,
            encrypted_value=encrypted_value,
            visibility=visibility,
            description=args.get("description"),
            tags=args.get("tags", []),
            relevance_score=1.0,
        )

        db.add(state)
        await db.commit()
        await db.refresh(state)

        return {
            "success": True,
            "message": f"Saved state: {store_ref}/{args['key']}",
            "state_id": str(state.id),
            "value": _decrypt_state_value(state),
        }

    @staticmethod
    async def _retrieve_state(
        db: AsyncSession,
        user_id: str,
        args: dict[str, Any],
        allowed_stores: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Retrieve state from store."""
        user_uuid = uuid_lib.UUID(user_id)
        store_ref = args.get("store")

        # If specific store requested, resolve it
        store = None
        if store_ref:
            store = await StateTools._resolve_store(db, store_ref)
            if not store:
                return {"error": f"Store '{store_ref}' not found"}

        # Build query
        visibility_filter = State.user_id == user_uuid

        # Also include shared states from allowed stores
        if allowed_stores:
            store_ids = []
            for s_ref in allowed_stores:
                s = await StateTools._resolve_store(db, s_ref)
                if s:
                    store_ids.append(s.id)
            if store_ids:
                visibility_filter = or_(
                    visibility_filter,
                    and_(State.visibility == "shared", State.store_id.in_(store_ids)),
                )

        query = select(State).where(
            and_(
                or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                visibility_filter,
            )
        )

        if store:
            query = query.where(State.store_id == store.id)

        if "key" in args and args["key"]:
            query = query.where(State.key == args["key"])

        if "search" in args and args["search"]:
            search_pattern = f"%{args['search']}%"
            query = query.where(
                or_(State.key.ilike(search_pattern), State.description.ilike(search_pattern))
            )

        if "tags" in args and args["tags"]:
            tag_list = [tag.strip() for tag in args["tags"].split(",")]
            for tag in tag_list:
                query = query.where(State.tags.contains([tag]))

        query = query.order_by(State.relevance_score.desc())
        limit = args.get("limit", 10)
        query = query.limit(limit)

        result = await db.execute(query)
        states = result.scalars().all()

        if not states:
            return {"success": True, "message": "No matching states found", "states": []}

        # Resolve store refs for display
        store_cache: dict[Any, Store] = {}
        state_list = []
        for s in states:
            if s.store_id not in store_cache:
                store_result = await db.execute(select(Store).where(Store.id == s.store_id))
                store_cache[s.store_id] = store_result.scalar_one_or_none()
            st = store_cache[s.store_id]
            state_list.append({
                "store": f"{st.namespace}/{st.name}" if st else "unknown",
                "key": s.key,
                "value": _decrypt_state_value(s),
                "description": s.description,
                "tags": s.tags,
                "visibility": s.visibility,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            })

        return {
            "success": True,
            "count": len(state_list),
            "states": state_list,
        }

    @staticmethod
    async def _update_state(
        db: AsyncSession, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Update existing state."""
        user_uuid = uuid_lib.UUID(user_id)
        store_ref = args.get("store", "")

        store = await StateTools._resolve_store(db, store_ref)
        if not store:
            return {"error": f"Store '{store_ref}' not found"}

        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.store_id == store.id,
                    State.key == args["key"],
                )
            )
        )
        state = result.scalar_one_or_none()

        if not state:
            return {
                "error": f"State not found for store '{store_ref}' and key '{args['key']}'",
                "suggestion": "Use save_state to create a new state entry",
            }

        # Schema validation
        if "value" in args and store.strict and store.schema:
            prop_schema = store.schema.get("properties", {}).get(args["key"])
            if prop_schema:
                try:
                    jsonschema.validate(instance=args["value"], schema=prop_schema)
                except jsonschema.ValidationError as e:
                    return {"error": f"Value validation failed for key '{args['key']}': {e.message}"}

        if "value" in args:
            if state.encrypted:
                state.encrypted_value = encryption_service.encrypt(json.dumps(args["value"]))
                state.value = {}
            else:
                state.value = args["value"]
        if "description" in args:
            state.description = args["description"]
        if "tags" in args:
            state.tags = args["tags"]

        await db.commit()
        await db.refresh(state)

        return {
            "success": True,
            "message": f"Updated state: {store_ref}/{args['key']}",
            "value": _decrypt_state_value(state),
        }

    @staticmethod
    async def _delete_state(
        db: AsyncSession, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Delete state."""
        user_uuid = uuid_lib.UUID(user_id)
        store_ref = args.get("store", "")

        store = await StateTools._resolve_store(db, store_ref)
        if not store:
            return {"error": f"Store '{store_ref}' not found"}

        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.store_id == store.id,
                    State.key == args["key"],
                )
            )
        )
        state = result.scalar_one_or_none()

        if not state:
            return {"error": f"State not found for store '{store_ref}' and key '{args['key']}'"}

        await db.delete(state)
        await db.commit()

        return {"success": True, "message": f"Deleted state: {store_ref}/{args['key']}"}

    @staticmethod
    async def get_relevant_contexts(
        db: AsyncSession,
        user_id: str,
        agent_id: Optional[str] = None,
        enabled_stores: Optional[list[dict[str, str]]] = None,
        limit: int = 5,
    ) -> list[State]:
        """Get relevant states for auto-injection into prompts."""
        user_uuid = uuid_lib.UUID(user_id)

        if not enabled_stores:
            return []

        # Resolve store refs
        store_refs = [s["store"] for s in enabled_stores]
        store_ids = []
        for ref in store_refs:
            store = await StateTools._resolve_store(db, ref)
            if store:
                store_ids.append(store.id)

        if not store_ids:
            return []

        visibility_filter = or_(
            State.user_id == user_uuid,
            and_(State.visibility == "shared", State.store_id.in_(store_ids)),
        )

        query = select(State).options(selectinload(State.store)).where(
            and_(
                State.store_id.in_(store_ids),
                or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                visibility_filter,
            )
        )

        query = query.order_by(State.relevance_score.desc()).limit(limit)

        result = await db.execute(query)
        return result.scalars().all()
