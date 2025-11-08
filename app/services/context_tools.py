"""Context store tools for LLM to save/retrieve context."""
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid as uuid_lib

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.context_store import ContextStore
from app.models.user import GroupMember


class ContextTools:
    """Provides LLM tools for interacting with context store."""

    @staticmethod
    def get_tool_definitions() -> List[Dict[str, Any]]:
        """
        Get OpenAI-compatible tool definitions for context operations.

        Returns:
            List of tool definitions
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "save_context",
                    "description": (
                        "Save information to context store for future recall. Use this to remember "
                        "user preferences, facts learned during conversation, important decisions, "
                        "or any information that should persist across conversations. "
                        "Examples: user's timezone, preferred communication style, project details, etc."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "namespace": {
                                "type": "string",
                                "description": (
                                    "Category/namespace for organization. Common values: "
                                    "'preferences' (user settings), 'facts' (learned information), "
                                    "'decisions' (important choices), 'project' (project-specific data)"
                                ),
                                "enum": ["preferences", "facts", "decisions", "project", "reminders", "goals"]
                            },
                            "key": {
                                "type": "string",
                                "description": "Unique identifier within the namespace (e.g., 'timezone', 'favorite_language')"
                            },
                            "value": {
                                "type": "object",
                                "description": "Data to store (as JSON object)"
                            },
                            "description": {
                                "type": "string",
                                "description": "Human-readable description of what this context contains"
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tags for categorization and search"
                            },
                            "visibility": {
                                "type": "string",
                                "enum": ["private", "group"],
                                "description": "Who can access this context: 'private' (user only) or 'group' (team members)",
                                "default": "private"
                            }
                        },
                        "required": ["namespace", "key", "value"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "retrieve_context",
                    "description": (
                        "Retrieve saved context by namespace and/or key. Use this to recall "
                        "previously saved information, preferences, or facts about the user or project."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "namespace": {
                                "type": "string",
                                "description": "Filter by namespace (e.g., 'preferences', 'facts')"
                            },
                            "key": {
                                "type": "string",
                                "description": "Specific key to retrieve (optional, omit to get all in namespace)"
                            },
                            "search": {
                                "type": "string",
                                "description": "Search term to find in keys and descriptions"
                            },
                            "tags": {
                                "type": "string",
                                "description": "Comma-separated tags to filter by"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return",
                                "default": 10
                            }
                        }
                    }
                }
            },
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
                                "description": "Namespace of the context to update"
                            },
                            "key": {
                                "type": "string",
                                "description": "Key of the context to update"
                            },
                            "value": {
                                "type": "object",
                                "description": "New value to store (replaces existing)"
                            },
                            "description": {
                                "type": "string",
                                "description": "Updated description"
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Updated tags"
                            }
                        },
                        "required": ["namespace", "key"]
                    }
                }
            },
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
                                "description": "Namespace of the context to delete"
                            },
                            "key": {
                                "type": "string",
                                "description": "Key of the context to delete"
                            }
                        },
                        "required": ["namespace", "key"]
                    }
                }
            }
        ]

    @staticmethod
    async def execute_tool(
        db: AsyncSession,
        tool_name: str,
        arguments: Dict[str, Any],
        user_id: str,
        chat_id: Optional[str] = None,
        group_id: Optional[str] = None,
        assistant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a context tool.

        Args:
            db: Database session
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_id: User ID
            chat_id: Optional chat ID
            group_id: Optional group ID
            assistant_id: Optional assistant ID

        Returns:
            Tool execution result
        """
        if tool_name == "save_context":
            return await ContextTools._save_context(
                db, user_id, arguments, group_id, assistant_id
            )
        elif tool_name == "retrieve_context":
            return await ContextTools._retrieve_context(
                db, user_id, arguments
            )
        elif tool_name == "update_context":
            return await ContextTools._update_context(
                db, user_id, arguments
            )
        elif tool_name == "delete_context":
            return await ContextTools._delete_context(
                db, user_id, arguments
            )
        else:
            return {"error": f"Unknown context tool: {tool_name}"}

    @staticmethod
    async def _save_context(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any],
        group_id: Optional[str],
        assistant_id: Optional[str]
    ) -> Dict[str, Any]:
        """Save context to store."""
        user_uuid = uuid_lib.UUID(user_id)

        # Check if context already exists
        result = await db.execute(
            select(ContextStore).where(
                and_(
                    ContextStore.user_id == user_uuid,
                    ContextStore.namespace == args["namespace"],
                    ContextStore.key == args["key"]
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            return {
                "error": f"Context already exists for namespace '{args['namespace']}' and key '{args['key']}'. Use update_context to modify it.",
                "existing_value": existing.value
            }

        # Validate visibility
        visibility = args.get("visibility", "private")
        group_uuid = None

        if visibility == "group":
            if not group_id:
                return {"error": "Group visibility requires a group context"}
            group_uuid = uuid_lib.UUID(group_id)

            # Verify user is member of the group
            result = await db.execute(
                select(GroupMember).where(
                    and_(
                        GroupMember.user_id == user_uuid,
                        GroupMember.group_id == group_uuid,
                        GroupMember.active == True
                    )
                )
            )
            if not result.scalar_one_or_none():
                return {"error": "User is not a member of the specified group"}

        # Create context
        context = ContextStore(
            user_id=user_uuid,
            group_id=group_uuid,
            assistant_id=uuid_lib.UUID(assistant_id) if assistant_id else None,
            namespace=args["namespace"],
            key=args["key"],
            value=args["value"],
            visibility=visibility,
            description=args.get("description"),
            tags=args.get("tags", []),
            relevance_score=1.0
        )

        db.add(context)
        await db.commit()
        await db.refresh(context)

        return {
            "success": True,
            "message": f"Saved context: {args['namespace']}/{args['key']}",
            "context_id": str(context.id),
            "value": context.value
        }

    @staticmethod
    async def _retrieve_context(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Retrieve context from store."""
        user_uuid = uuid_lib.UUID(user_id)

        # Get user's groups for group context access
        result = await db.execute(
            select(GroupMember.group_id).where(
                and_(
                    GroupMember.user_id == user_uuid,
                    GroupMember.active == True
                )
            )
        )
        user_groups = [row[0] for row in result.all()]

        # Build query - user's own contexts + group contexts
        query = select(ContextStore).where(
            and_(
                or_(
                    ContextStore.expires_at == None,
                    ContextStore.expires_at > datetime.utcnow()
                ),
                or_(
                    ContextStore.user_id == user_uuid,
                    and_(
                        ContextStore.visibility == "group",
                        ContextStore.group_id.in_(user_groups) if user_groups else False
                    )
                )
            )
        )

        # Apply filters
        if "namespace" in args and args["namespace"]:
            query = query.where(ContextStore.namespace == args["namespace"])

        if "key" in args and args["key"]:
            query = query.where(ContextStore.key == args["key"])

        if "search" in args and args["search"]:
            search_pattern = f"%{args['search']}%"
            query = query.where(
                or_(
                    ContextStore.key.ilike(search_pattern),
                    ContextStore.description.ilike(search_pattern)
                )
            )

        if "tags" in args and args["tags"]:
            tag_list = [tag.strip() for tag in args["tags"].split(',')]
            for tag in tag_list:
                query = query.where(ContextStore.tags.contains([tag]))

        # Order by relevance and limit
        query = query.order_by(ContextStore.relevance_score.desc())
        limit = args.get("limit", 10)
        query = query.limit(limit)

        result = await db.execute(query)
        contexts = result.scalars().all()

        if not contexts:
            return {
                "success": True,
                "message": "No matching contexts found",
                "contexts": []
            }

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
                    "updated_at": ctx.updated_at.isoformat()
                }
                for ctx in contexts
            ]
        }

    @staticmethod
    async def _update_context(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update existing context."""
        user_uuid = uuid_lib.UUID(user_id)

        # Find context
        result = await db.execute(
            select(ContextStore).where(
                and_(
                    ContextStore.user_id == user_uuid,
                    ContextStore.namespace == args["namespace"],
                    ContextStore.key == args["key"]
                )
            )
        )
        context = result.scalar_one_or_none()

        if not context:
            return {
                "error": f"Context not found for namespace '{args['namespace']}' and key '{args['key']}'",
                "suggestion": "Use save_context to create a new context entry"
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
            "value": context.value
        }

    @staticmethod
    async def _delete_context(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Delete context."""
        user_uuid = uuid_lib.UUID(user_id)

        # Find context
        result = await db.execute(
            select(ContextStore).where(
                and_(
                    ContextStore.user_id == user_uuid,
                    ContextStore.namespace == args["namespace"],
                    ContextStore.key == args["key"]
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

        return {
            "success": True,
            "message": f"Deleted context: {args['namespace']}/{args['key']}"
        }

    @staticmethod
    async def get_relevant_contexts(
        db: AsyncSession,
        user_id: str,
        assistant_id: Optional[str] = None,
        group_id: Optional[str] = None,
        namespaces: Optional[List[str]] = None,
        limit: int = 5
    ) -> List[ContextStore]:
        """
        Get relevant contexts for auto-injection into prompts.

        Args:
            db: Database session
            user_id: User ID
            assistant_id: Optional assistant ID to filter contexts
            group_id: Optional group ID to include group contexts
            namespaces: Optional list of namespaces to include
            limit: Maximum number of contexts to return

        Returns:
            List of relevant context entries
        """
        user_uuid = uuid_lib.UUID(user_id)

        # Get user's groups
        result = await db.execute(
            select(GroupMember.group_id).where(
                and_(
                    GroupMember.user_id == user_uuid,
                    GroupMember.active == True
                )
            )
        )
        user_groups = [row[0] for row in result.all()]

        # Build query
        query = select(ContextStore).where(
            and_(
                or_(
                    ContextStore.expires_at == None,
                    ContextStore.expires_at > datetime.utcnow()
                ),
                or_(
                    ContextStore.user_id == user_uuid,
                    and_(
                        ContextStore.visibility == "group",
                        ContextStore.group_id.in_(user_groups) if user_groups else False
                    )
                )
            )
        )

        # Filter by assistant if provided
        if assistant_id:
            assistant_uuid = uuid_lib.UUID(assistant_id)
            query = query.where(
                or_(
                    ContextStore.assistant_id == assistant_uuid,
                    ContextStore.assistant_id == None
                )
            )

        # Filter by namespaces if provided
        if namespaces:
            query = query.where(ContextStore.namespace.in_(namespaces))

        # Order by relevance and limit
        query = query.order_by(ContextStore.relevance_score.desc()).limit(limit)

        result = await db.execute(query)
        return result.scalars().all()
