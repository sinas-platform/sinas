"""Context store tools for LLM to save/retrieve context."""
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.state import State


class StateTools:
    """Provides LLM tools for interacting with context store."""

    @staticmethod
    async def get_tool_definitions(
        db: Optional[AsyncSession] = None,
        user_id: Optional[str] = None,
        agent_state_namespaces_readonly: Optional[list[str]] = None,
        agent_state_namespaces_readwrite: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """
        Get OpenAI-compatible tool definitions for state operations.

        Args:
            db: Optional database session for enriching tool descriptions
            user_id: Optional user ID for personalizing tool descriptions
            agent_state_namespaces_readonly: Readonly state namespaces (retrieve only)
            agent_state_namespaces_readwrite: Read-write state namespaces (full access)

        Returns:
            List of tool definitions
        """
        # Normalize inputs
        readonly_namespaces = agent_state_namespaces_readonly or []
        readwrite_namespaces = agent_state_namespaces_readwrite or []
        all_namespaces = readonly_namespaces + readwrite_namespaces

        # Get available context keys if db and user_id provided
        available_keys_info = ""
        if db and user_id:
            available_keys_info = await StateTools._get_available_keys_description(
                db, user_id, allowed_namespaces=all_namespaces or None
            )

        # Opt-in: if no namespaces at all, return no tools
        if len(all_namespaces) == 0:
            return []

        # Build namespace info for allowed namespaces
        readonly_ns_list = (
            ", ".join([f"'{ns}'" for ns in readonly_namespaces]) if readonly_namespaces else ""
        )
        readwrite_ns_list = (
            ", ".join([f"'{ns}'" for ns in readwrite_namespaces]) if readwrite_namespaces else ""
        )

        namespace_info = ""
        if readwrite_namespaces:
            namespace_info += f"\n\nRead-write namespaces: {readwrite_ns_list}. You can save/update/delete context in these namespaces."
        if readonly_namespaces:
            namespace_info += f"\n\nRead-only namespaces: {readonly_ns_list}. You can only retrieve context from these namespaces."

        save_description = (
            "Save information to context store for future recall. Use this to remember "
            "user preferences, facts learned during conversation, important decisions, "
            "or any information that should persist across conversations. "
            "Examples: user's timezone, preferred communication style, project details, etc."
        )
        if namespace_info:
            save_description += namespace_info

        retrieve_description = (
            "Retrieve saved context by namespace and/or key. Use this to recall "
            "previously saved information, preferences, or facts about the user or project."
        )

        if available_keys_info:
            retrieve_description += f"\n\n{available_keys_info}"

        tools = []

        # Always include retrieve_context for all namespaces (readonly + readwrite)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "retrieve_context",
                    "description": retrieve_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "namespace": {
                                "type": "string",
                                "description": "Filter by namespace (e.g., 'preferences', 'facts')",
                            },
                            "key": {
                                "type": "string",
                                "description": "Specific key to retrieve (optional, omit to get all in namespace)",
                            },
                            "search": {
                                "type": "string",
                                "description": "Search term to find in keys and descriptions",
                            },
                            "tags": {
                                "type": "string",
                                "description": "Comma-separated tags to filter by",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return",
                                "default": 10,
                            },
                        },
                    },
                },
            }
        )

        # Only include write tools if there are readwrite namespaces
        if readwrite_namespaces:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "save_context",
                        "description": save_description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "namespace": {
                                    "type": "string",
                                    "description": (
                                        "Category/namespace for organization. Use one of the read-write namespaces."
                                    ),
                                    "enum": readwrite_namespaces,  # Only allow readwrite namespaces
                                },
                                "key": {
                                    "type": "string",
                                    "description": "Unique identifier within the namespace (e.g., 'timezone', 'favorite_language')",
                                },
                                "value": {
                                    "type": "object",
                                    "description": "Data to store (as JSON object)",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Human-readable description of what this context contains",
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Tags for categorization and search",
                                },
                                "visibility": {
                                    "type": "string",
                                    "enum": ["private", "shared"],
                                    "description": "Who can access this context: 'private' (user only) or 'group' (permitted users)",
                                    "default": "private",
                                },
                            },
                            "required": ["namespace", "key", "value"],
                        },
                    },
                }
            )

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "update_context",
                        "description": (
                            "Update existing context entry. Use this to modify previously saved information "
                            "when new details are learned or preferences change."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "namespace": {
                                    "type": "string",
                                    "description": "Namespace of the context to update",
                                    "enum": readwrite_namespaces,
                                },
                                "key": {
                                    "type": "string",
                                    "description": "Key of the context to update",
                                },
                                "value": {
                                    "type": "object",
                                    "description": "New value to store (replaces existing)",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Updated description",
                                },
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Updated tags",
                                },
                            },
                            "required": ["namespace", "key"],
                        },
                    },
                }
            )

            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "delete_context",
                        "description": "Delete a context entry when it's no longer needed or is outdated.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "namespace": {
                                    "type": "string",
                                    "description": "Namespace of the context to delete",
                                    "enum": readwrite_namespaces,
                                },
                                "key": {
                                    "type": "string",
                                    "description": "Key of the context to delete",
                                },
                            },
                            "required": ["namespace", "key"],
                        },
                    },
                }
            )

        return tools

    @staticmethod
    async def _get_available_keys_description(
        db: AsyncSession,
        user_id: str,
        allowed_namespaces: Optional[list[str]] = None,
    ) -> str:
        """
        Get a summary of available context keys for this user.

        Args:
            db: Database session
            user_id: User ID
            allowed_namespaces: Namespaces the agent may access (restricts shared state visibility)

        Returns:
            Formatted string describing available context keys
        """
        user_uuid = uuid_lib.UUID(user_id)

        # Own states are always visible; shared states only in allowed namespaces
        visibility_filter = State.user_id == user_uuid
        if allowed_namespaces:
            visibility_filter = or_(
                visibility_filter,
                and_(State.visibility == "shared", State.namespace.in_(allowed_namespaces)),
            )

        query = (
            select(State.namespace, State.key, State.description)
            .where(
                and_(
                    or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                    visibility_filter,
                )
            )
            .order_by(State.namespace, State.key)
        )

        result = await db.execute(query)
        contexts = result.all()

        if not contexts:
            return ""

        # Group by namespace
        by_namespace: dict[str, list[tuple]] = {}
        for namespace, key, description in contexts:
            if namespace not in by_namespace:
                by_namespace[namespace] = []
            by_namespace[namespace].append((key, description))

        # Format as readable list
        lines = ["Currently available context:"]
        for namespace in sorted(by_namespace.keys()):
            keys = by_namespace[namespace]
            key_list = ", ".join([f"'{key}'" for key, _ in keys])
            lines.append(f"  • {namespace}: {key_list}")

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
        """
        Execute a context tool.

        Args:
            db: Database session
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_id: User ID
            chat_id: Optional chat ID
                        agent_id: Optional agent ID for namespace validation

        Returns:
            Tool execution result
        """
        # Get agent's allowed context namespaces for validation
        write_namespaces = None
        all_allowed_namespaces = None
        if agent_id:
            from app.models.agent import Agent

            result = await db.execute(select(Agent).where(Agent.id == uuid_lib.UUID(agent_id)))
            agent = result.scalar_one_or_none()
            if agent:
                write_namespaces = agent.state_namespaces_readwrite
                all_allowed_namespaces = (agent.state_namespaces_readonly or []) + (
                    agent.state_namespaces_readwrite or []
                ) or None

        # Check namespace access for write operations
        if tool_name in ["save_context", "update_context"] and write_namespaces is not None:
            requested_namespace = arguments.get("namespace")
            if not requested_namespace or requested_namespace not in write_namespaces:
                return {
                    "error": f"Agent not authorized to write to namespace '{requested_namespace}'",
                    "allowed_namespaces": write_namespaces if write_namespaces else [],
                }

        if tool_name == "save_context":
            return await StateTools._save_context(
                db,
                user_id,
                arguments,
            )
        elif tool_name == "retrieve_context":
            return await StateTools._retrieve_context(
                db, user_id, arguments, allowed_namespaces=all_allowed_namespaces
            )
        elif tool_name == "update_context":
            return await StateTools._update_context(db, user_id, arguments)
        elif tool_name == "delete_context":
            return await StateTools._delete_context(db, user_id, arguments)
        else:
            return {"error": f"Unknown context tool: {tool_name}"}

    @staticmethod
    async def _save_context(
        db: AsyncSession,
        user_id: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Save context to store."""
        user_uuid = uuid_lib.UUID(user_id)

        # Check if context already exists
        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.namespace == args["namespace"],
                    State.key == args["key"],
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            return {
                "error": f"Context already exists for namespace '{args['namespace']}' and key '{args['key']}'. Use update_context to modify it.",
                "existing_value": existing.value,
            }

        # Validate visibility
        visibility = args.get("visibility", "private")
        # Shared states can be created - namespace permission check happens at API level

        # Create context
        context = State(
            user_id=user_uuid,
            namespace=args["namespace"],
            key=args["key"],
            value=args["value"],
            visibility=visibility,
            description=args.get("description"),
            tags=args.get("tags", []),
            relevance_score=1.0,
        )

        db.add(context)
        await db.commit()
        await db.refresh(context)

        return {
            "success": True,
            "message": f"Saved context: {args['namespace']}/{args['key']}",
            "context_id": str(context.id),
            "value": context.value,
        }

    @staticmethod
    async def _retrieve_context(
        db: AsyncSession,
        user_id: str,
        args: dict[str, Any],
        allowed_namespaces: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Retrieve context from store."""
        user_uuid = uuid_lib.UUID(user_id)

        # Own states always visible; shared states only in allowed namespaces
        visibility_filter = State.user_id == user_uuid
        if allowed_namespaces:
            visibility_filter = or_(
                visibility_filter,
                and_(State.visibility == "shared", State.namespace.in_(allowed_namespaces)),
            )

        query = select(State).where(
            and_(
                or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                visibility_filter,
            )
        )

        # Apply filters
        if "namespace" in args and args["namespace"]:
            query = query.where(State.namespace == args["namespace"])

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

        # Order by relevance and limit
        query = query.order_by(State.relevance_score.desc())
        limit = args.get("limit", 10)
        query = query.limit(limit)

        result = await db.execute(query)
        contexts = result.scalars().all()

        if not contexts:
            return {"success": True, "message": "No matching contexts found", "contexts": []}

        return {
            "success": True,
            "count": len(contexts),
            "contexts": [
                {
                    "namespace": ctx.namespace,
                    "key": ctx.key,
                    "value": ctx.value,
                    "description": ctx.description,
                    "tags": ctx.tags,
                    "visibility": ctx.visibility,
                    "created_at": ctx.created_at.isoformat(),
                    "updated_at": ctx.updated_at.isoformat(),
                }
                for ctx in contexts
            ],
        }

    @staticmethod
    async def _update_context(
        db: AsyncSession, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Update existing context."""
        user_uuid = uuid_lib.UUID(user_id)

        # Find context
        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.namespace == args["namespace"],
                    State.key == args["key"],
                )
            )
        )
        context = result.scalar_one_or_none()

        if not context:
            return {
                "error": f"Context not found for namespace '{args['namespace']}' and key '{args['key']}'",
                "suggestion": "Use save_context to create a new context entry",
            }

        # Update fields
        if "value" in args:
            context.value = args["value"]
        if "description" in args:
            context.description = args["description"]
        if "tags" in args:
            context.tags = args["tags"]

        await db.commit()
        await db.refresh(context)

        return {
            "success": True,
            "message": f"Updated context: {args['namespace']}/{args['key']}",
            "value": context.value,
        }

    @staticmethod
    async def _delete_context(
        db: AsyncSession, user_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Delete context."""
        user_uuid = uuid_lib.UUID(user_id)

        # Find context
        result = await db.execute(
            select(State).where(
                and_(
                    State.user_id == user_uuid,
                    State.namespace == args["namespace"],
                    State.key == args["key"],
                )
            )
        )
        context = result.scalar_one_or_none()

        if not context:
            return {
                "error": f"Context not found for namespace '{args['namespace']}' and key '{args['key']}'"
            }

        await db.delete(context)
        await db.commit()

        return {"success": True, "message": f"Deleted context: {args['namespace']}/{args['key']}"}

    @staticmethod
    async def get_relevant_contexts(
        db: AsyncSession,
        user_id: str,
        agent_id: Optional[str] = None,
        namespaces: Optional[list[str]] = None,
        limit: int = 5,
    ) -> list[State]:
        """
        Get relevant contexts for auto-injection into prompts.

        Args:
            db: Database session
            user_id: User ID
            agent_id: Optional agent ID to filter contexts
                        namespaces: Optional list of namespaces to include
            limit: Maximum number of contexts to return

        Returns:
            List of relevant context entries
        """
        user_uuid = uuid_lib.UUID(user_id)

        # Own states always visible; shared states only in allowed namespaces
        visibility_filter = State.user_id == user_uuid
        if namespaces:
            visibility_filter = or_(
                visibility_filter,
                and_(State.visibility == "shared", State.namespace.in_(namespaces)),
            )

        query = select(State).where(
            and_(
                or_(State.expires_at == None, State.expires_at > datetime.utcnow()),
                visibility_filter,
            )
        )

        # Filter by namespaces if provided
        if namespaces:
            query = query.where(State.namespace.in_(namespaces))

        # Order by relevance and limit
        query = query.order_by(State.relevance_score.desc()).limit(limit)

        result = await db.execute(query)
        return result.scalars().all()
