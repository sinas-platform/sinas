"""Ontology tools for LLM to query and write business data."""
from typing import List, Dict, Any, Optional
import uuid as uuid_lib

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology import (
    Concept, Property, Relationship, DataSource,
    ConceptQuery, Endpoint, EndpointProperty, EndpointFilter
)
from app.models.user import GroupMember


class OntologyTools:
    """Provides LLM tools for interacting with ontology and business data."""

    @staticmethod
    def get_tool_definitions() -> List[Dict[str, Any]]:
        """
        Get OpenAI-compatible tool definitions for ontology operations.

        Returns:
            List of tool definitions
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "explore_ontology",
                    "description": (
                        "Explore the business ontology to understand available concepts (entities), "
                        "their properties (attributes), and relationships. Use this to discover what "
                        "business data is available and how different entities relate to each other. "
                        "Examples: exploring customer concepts, finding product properties, discovering "
                        "order-customer relationships."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept_name": {
                                "type": "string",
                                "description": "Name of a specific concept to explore (e.g., 'Customer', 'Order')"
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Namespace to filter concepts by (e.g., 'sales', 'inventory')"
                            },
                            "include_properties": {
                                "type": "boolean",
                                "description": "Include properties/attributes of concepts",
                                "default": True
                            },
                            "include_relationships": {
                                "type": "boolean",
                                "description": "Include relationships between concepts",
                                "default": True
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_business_data",
                    "description": (
                        "Query business data using the ontology layer. Provide the concept name "
                        "and optional filters to retrieve structured business data. This executes "
                        "pre-configured queries against the underlying data sources. "
                        "Examples: fetching customers, retrieving orders, getting product inventory."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "Concept name to query (e.g., 'Customer', 'Order', 'Product')"
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Namespace of the concept (if known)"
                            },
                            "properties": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Specific properties to retrieve (if not specified, returns all)"
                            },
                            "filters": {
                                "type": "object",
                                "description": (
                                    "Filters to apply (e.g., {'customer_id': '123', 'status': 'active'}). "
                                    "Available operators will be shown after exploring the concept."
                                )
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of records to return",
                                "default": 10
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Offset for pagination",
                                "default": 0
                            }
                        },
                        "required": ["concept"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_ontology_data_record",
                    "description": (
                        "Create a new data record for self-managed ontology concepts (concepts stored "
                        "directly in SINAS database). Only works for concepts marked as self-managed. "
                        "Examples: creating a new customer record, adding a new product, registering an order."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "Self-managed concept name (e.g., 'Customer', 'Order')"
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Namespace of the concept"
                            },
                            "data": {
                                "type": "object",
                                "description": "Property values for the new record (e.g., {'name': 'John', 'email': 'john@example.com'})"
                            }
                        },
                        "required": ["concept", "data"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_ontology_data_record",
                    "description": (
                        "Update an existing ontology data record for self-managed concepts. "
                        "Only works for concepts stored in SINAS database."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "Self-managed concept name"
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Namespace of the concept"
                            },
                            "record_id": {
                                "type": "string",
                                "description": "ID of the record to update"
                            },
                            "data": {
                                "type": "object",
                                "description": "Property values to update"
                            }
                        },
                        "required": ["concept", "record_id", "data"]
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
        group_id: Optional[str] = None,
        assistant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute an ontology tool.

        Args:
            db: Database session
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_id: User ID
            group_id: Optional group ID for group-scoped ontology access
            assistant_id: Optional assistant ID for checking ontology access restrictions

        Returns:
            Tool execution result
        """
        # Check assistant-level restrictions before executing
        if assistant_id and tool_name in ["explore_ontology", "query_business_data", "create_ontology_data_record", "update_ontology_data_record"]:
            can_access = await OntologyTools._check_assistant_ontology_access(
                db, assistant_id, arguments
            )
            if not can_access:
                return {
                    "error": "Assistant is not authorized to access this ontology namespace/concept",
                    "suggestion": "Configure assistant's ontology_namespaces and ontology_concepts to allow access"
                }
        if tool_name == "explore_ontology":
            return await OntologyTools._explore_ontology(
                db, user_id, arguments, group_id
            )
        elif tool_name == "query_business_data":
            return await OntologyTools._query_business_data(
                db, user_id, arguments, group_id
            )
        elif tool_name == "create_ontology_data_record":
            return await OntologyTools._create_ontology_data_record(
                db, user_id, arguments, group_id
            )
        elif tool_name == "update_ontology_data_record":
            return await OntologyTools._update_ontology_data_record(
                db, user_id, arguments, group_id
            )
        else:
            return {"error": f"Unknown ontology tool: {tool_name}"}

    @staticmethod
    async def _check_assistant_ontology_access(
        db: AsyncSession,
        assistant_id: str,
        arguments: Dict[str, Any]
    ) -> bool:
        """
        Check if assistant is allowed to access the requested ontology namespace/concept.

        Args:
            db: Database session
            assistant_id: Assistant ID
            arguments: Tool arguments containing namespace and/or concept

        Returns:
            True if access is allowed, False otherwise
        """
        from app.models.assistant import Assistant

        # Get assistant
        result = await db.execute(
            select(Assistant).where(Assistant.id == uuid_lib.UUID(assistant_id))
        )
        assistant = result.scalar_one_or_none()

        if not assistant:
            return True  # If assistant doesn't exist, don't restrict

        # If both are None, assistant has access to everything
        if assistant.ontology_namespaces is None and assistant.ontology_concepts is None:
            return True

        # Extract namespace and concept from arguments
        namespace = arguments.get("namespace")
        concept = arguments.get("concept")

        # Check namespace restriction
        if assistant.ontology_namespaces is not None:
            if namespace and namespace not in assistant.ontology_namespaces:
                return False

        # Check concept restriction (format: namespace.concept)
        if assistant.ontology_concepts is not None:
            if concept:
                # If namespace is provided, check namespace.concept
                if namespace:
                    full_concept = f"{namespace}.{concept}"
                    if full_concept not in assistant.ontology_concepts:
                        # Also check if just concept name is in the list
                        if concept not in assistant.ontology_concepts:
                            return False
                else:
                    # No namespace provided, just check concept
                    if concept not in assistant.ontology_concepts:
                        return False

        return True

    @staticmethod
    async def _get_user_groups(db: AsyncSession, user_id: str) -> List[uuid_lib.UUID]:
        """Get all group IDs that the user is a member of."""
        user_uuid = uuid_lib.UUID(user_id)
        result = await db.execute(
            select(GroupMember.group_id).where(
                and_(
                    GroupMember.user_id == user_uuid,
                    GroupMember.active == True
                )
            )
        )
        return [row[0] for row in result.all()]

    @staticmethod
    async def _explore_ontology(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any],
        group_id: Optional[str]
    ) -> Dict[str, Any]:
        """Explore the ontology structure."""
        # Get user's groups
        user_groups = await OntologyTools._get_user_groups(db, user_id)

        if group_id:
            # Filter by specific group
            group_filter = Concept.group_id == uuid_lib.UUID(group_id)
        else:
            # Include all user's groups
            group_filter = Concept.group_id.in_(user_groups) if user_groups else False

        # Build query
        query = select(Concept).where(group_filter)

        if "concept_name" in args and args["concept_name"]:
            query = query.where(Concept.name.ilike(f"%{args['concept_name']}%"))

        if "namespace" in args and args["namespace"]:
            query = query.where(Concept.namespace == args["namespace"])

        result = await db.execute(query)
        concepts = result.scalars().all()

        if not concepts:
            return {
                "success": True,
                "message": "No matching concepts found in your accessible groups",
                "concepts": []
            }

        # Build response
        concept_data = []
        for concept in concepts:
            concept_info = {
                "namespace": concept.namespace,
                "name": concept.name,
                "display_name": concept.display_name,
                "description": concept.description,
                "is_self_managed": concept.is_self_managed
            }

            # Include properties if requested
            if args.get("include_properties", True):
                result = await db.execute(
                    select(Property).where(Property.concept_id == concept.id)
                )
                properties = result.scalars().all()
                concept_info["properties"] = [
                    {
                        "name": prop.name,
                        "display_name": prop.display_name,
                        "description": prop.description,
                        "data_type": prop.data_type.value,
                        "is_identifier": prop.is_identifier,
                        "is_required": prop.is_required
                    }
                    for prop in properties
                ]

            # Include relationships if requested
            if args.get("include_relationships", True):
                result = await db.execute(
                    select(Relationship).where(
                        or_(
                            Relationship.from_concept_id == concept.id,
                            Relationship.to_concept_id == concept.id
                        )
                    )
                )
                relationships = result.scalars().all()

                concept_info["relationships"] = []
                for rel in relationships:
                    # Get related concept
                    related_concept_id = (
                        rel.to_concept_id if rel.from_concept_id == concept.id
                        else rel.from_concept_id
                    )
                    result = await db.execute(
                        select(Concept).where(Concept.id == related_concept_id)
                    )
                    related_concept = result.scalar_one_or_none()

                    if related_concept:
                        concept_info["relationships"].append({
                            "name": rel.name,
                            "cardinality": rel.cardinality.value,
                            "direction": "from" if rel.from_concept_id == concept.id else "to",
                            "related_concept": f"{related_concept.namespace}.{related_concept.name}",
                            "description": rel.description
                        })

            concept_data.append(concept_info)

        return {
            "success": True,
            "count": len(concept_data),
            "concepts": concept_data
        }

    @staticmethod
    async def _query_business_data(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any],
        group_id: Optional[str]
    ) -> Dict[str, Any]:
        """Query business data through the ontology layer."""
        # Get user's groups
        user_groups = await OntologyTools._get_user_groups(db, user_id)

        if group_id:
            group_filter = Concept.group_id == uuid_lib.UUID(group_id)
        else:
            group_filter = Concept.group_id.in_(user_groups) if user_groups else False

        # Find the concept
        query = select(Concept).where(
            and_(
                group_filter,
                Concept.name == args["concept"]
            )
        )

        if "namespace" in args and args["namespace"]:
            query = query.where(Concept.namespace == args["namespace"])

        result = await db.execute(query)
        concept = result.scalar_one_or_none()

        if not concept:
            return {
                "error": f"Concept '{args['concept']}' not found in accessible groups",
                "suggestion": "Use explore_ontology to see available concepts"
            }

        # For now, return a message that direct querying requires endpoint setup
        # In a full implementation, this would execute the ConceptQuery
        return {
            "success": False,
            "message": (
                f"Direct querying of concept '{concept.namespace}.{concept.name}' requires "
                "endpoint configuration. Use the ontology API endpoints or set up a "
                "configured endpoint for this concept."
            ),
            "concept_info": {
                "namespace": concept.namespace,
                "name": concept.name,
                "is_self_managed": concept.is_self_managed,
                "has_query": concept.concept_query is not None
            }
        }

    @staticmethod
    async def _create_ontology_data_record(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any],
        group_id: Optional[str]
    ) -> Dict[str, Any]:
        """Create a new ontology data record for self-managed concepts."""
        from app.api.v1.endpoints.ontology_records import (
            get_user_group_ids, get_concept_with_permissions,
            get_dynamic_table_name, validate_data_against_properties
        )
        from sqlalchemy import text
        from datetime import datetime

        user_uuid = uuid_lib.UUID(user_id)

        # Get user's groups
        user_groups = await get_user_group_ids(db, user_uuid)

        # Find the concept
        try:
            # Determine namespace - if not provided, try to find concept by name
            namespace = args.get("namespace")
            if not namespace:
                # Try to find by concept name alone
                result = await db.execute(
                    select(Concept).where(
                        and_(
                            Concept.name == args["concept"],
                            Concept.is_self_managed == True,
                            Concept.group_id.in_(user_groups) if user_groups else False
                        )
                    )
                )
                concept = result.scalar_one_or_none()
                if concept:
                    namespace = concept.namespace
                else:
                    return {
                        "error": f"Self-managed concept '{args['concept']}' not found",
                        "suggestion": "Provide both namespace and concept name, or use explore_ontology to find available concepts."
                    }

            concept = await get_concept_with_permissions(
                db, namespace, args["concept"], user_groups
            )
        except Exception as e:
            return {
                "error": f"Concept not found: {str(e)}",
                "suggestion": "Use explore_ontology to see available self-managed concepts."
            }

        # Get concept properties
        result = await db.execute(
            select(Property).where(Property.concept_id == concept.id)
        )
        properties = result.scalars().all()

        # Validate data
        try:
            validated_data = validate_data_against_properties(
                args["data"], properties, is_create=True
            )
        except Exception as e:
            return {"error": f"Validation failed: {str(e)}"}

        # Generate ID and timestamps
        record_id = str(uuid_lib.uuid4())
        validated_data['id'] = record_id
        validated_data['created_at'] = datetime.utcnow()
        validated_data['updated_at'] = datetime.utcnow()

        # Get table name
        table_name = get_dynamic_table_name(concept)

        # Build INSERT statement
        columns = list(validated_data.keys())
        placeholders = [f":{col}" for col in columns]

        insert_sql = text(f"""
            INSERT INTO {table_name} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
        """)

        try:
            await db.execute(insert_sql, validated_data)
            await db.commit()
        except Exception as e:
            await db.rollback()
            return {"error": f"Failed to create record: {str(e)}"}

        return {
            "success": True,
            "message": f"Created record in {concept.namespace}.{concept.name}",
            "record_id": record_id,
            "data": validated_data
        }

    @staticmethod
    async def _update_ontology_data_record(
        db: AsyncSession,
        user_id: str,
        args: Dict[str, Any],
        group_id: Optional[str]
    ) -> Dict[str, Any]:
        """Update an existing ontology data record for self-managed concepts."""
        from app.api.v1.endpoints.ontology_records import (
            get_user_group_ids, get_concept_with_permissions,
            get_dynamic_table_name, validate_data_against_properties
        )
        from sqlalchemy import text
        from datetime import datetime

        user_uuid = uuid_lib.UUID(user_id)

        # Get user's groups
        user_groups = await get_user_group_ids(db, user_uuid)

        # Find the concept
        try:
            namespace = args.get("namespace")
            if not namespace:
                # Try to find by concept name alone
                result = await db.execute(
                    select(Concept).where(
                        and_(
                            Concept.name == args["concept"],
                            Concept.is_self_managed == True,
                            Concept.group_id.in_(user_groups) if user_groups else False
                        )
                    )
                )
                concept = result.scalar_one_or_none()
                if concept:
                    namespace = concept.namespace
                else:
                    return {
                        "error": f"Self-managed concept '{args['concept']}' not found",
                        "suggestion": "Provide both namespace and concept name."
                    }

            concept = await get_concept_with_permissions(
                db, namespace, args["concept"], user_groups
            )
        except Exception as e:
            return {"error": f"Concept not found: {str(e)}"}

        # Get concept properties
        result = await db.execute(
            select(Property).where(Property.concept_id == concept.id)
        )
        properties = result.scalars().all()

        # Validate data
        try:
            validated_data = validate_data_against_properties(
                args["data"], properties, is_create=False
            )
        except Exception as e:
            return {"error": f"Validation failed: {str(e)}"}

        # Add updated_at timestamp
        validated_data['updated_at'] = datetime.utcnow()

        # Get table name
        table_name = get_dynamic_table_name(concept)

        # Build UPDATE statement
        set_clauses = [f"{col} = :{col}" for col in validated_data.keys()]
        validated_data['record_id'] = args["record_id"]

        update_sql = text(f"""
            UPDATE {table_name}
            SET {', '.join(set_clauses)}
            WHERE id = :record_id
        """)

        try:
            result = await db.execute(update_sql, validated_data)
            await db.commit()

            if result.rowcount == 0:
                return {"error": "Record not found"}
        except Exception as e:
            await db.rollback()
            return {"error": f"Failed to update record: {str(e)}"}

        # Fetch updated record
        select_sql = text(f"SELECT * FROM {table_name} WHERE id = :record_id")
        result = await db.execute(select_sql, {"record_id": args["record_id"]})
        row = result.mappings().one_or_none()

        if not row:
            return {"error": "Record not found after update"}

        return {
            "success": True,
            "message": f"Updated record in {concept.namespace}.{concept.name}",
            "record_id": args["record_id"],
            "data": dict(row)
        }
