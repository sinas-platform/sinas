"""Query-to-tool converter for LLM tool calling."""
import logging
import time
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.permissions import check_permission
from app.models.query import Query
from app.services.database_pool import DatabasePoolManager
from app.services.template_renderer import render_function_parameters

logger = logging.getLogger(__name__)


class QueryToolConverter:
    """Converts queries to OpenAI tool format and manages execution."""

    async def get_available_queries(
        self,
        db: AsyncSession,
        user_id: str,
        enabled_queries: Optional[list[str]] = None,
        query_parameters: Optional[dict[str, dict[str, Any]]] = None,
        agent_input_context: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Get queries and convert to OpenAI tools format.

        Args:
            db: Database session
            user_id: User ID
            enabled_queries: List of "namespace/name" strings to include
            query_parameters: Pre-filled parameters with Jinja2 templates
            agent_input_context: Context for rendering Jinja2 templates
        """
        tools = []

        if not enabled_queries:
            return tools

        for query_ref in enabled_queries:
            if "/" not in query_ref:
                continue

            namespace, name = query_ref.split("/", 1)

            query = await Query.get_by_name(db, namespace, name)
            if not query or not query.is_active:
                continue

            # Parse locked vs overridable parameters
            locked_params = {}
            overridable_params = {}

            if query_parameters and query_ref in query_parameters:
                param_templates = query_parameters[query_ref]

                for param_name, param_config in param_templates.items():
                    if isinstance(param_config, dict) and "value" in param_config:
                        param_value = param_config["value"]
                        is_locked = param_config.get("locked", False)

                        if is_locked:
                            locked_params[param_name] = param_value
                        else:
                            overridable_params[param_name] = param_value
                    else:
                        overridable_params[param_name] = param_config

                # Render Jinja2 templates
                if agent_input_context:
                    try:
                        locked_params = render_function_parameters(
                            locked_params, agent_input_context
                        )
                        overridable_params = render_function_parameters(
                            overridable_params, agent_input_context
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to render query parameters for {query_ref}: {e}"
                        )
                        locked_params = {}
                        overridable_params = {}

            tool = self._query_to_tool(query, locked_params, overridable_params)
            tools.append(tool)

        return tools

    def _query_to_tool(
        self,
        query: Query,
        locked_params: Optional[dict[str, Any]] = None,
        overridable_params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Convert a query to OpenAI tool format."""
        description = query.description or f"Execute query {query.namespace}/{query.name}"

        parameters = query.input_schema.copy() if query.input_schema else {"type": "object"}
        if "properties" not in parameters:
            parameters["properties"] = {}

        # Process locked parameters: hide from schema
        if locked_params and "properties" in parameters:
            for param_name in locked_params.keys():
                if param_name in parameters["properties"]:
                    del parameters["properties"][param_name]
                    if "required" in parameters and param_name in parameters["required"]:
                        parameters["required"] = [
                            r for r in parameters["required"] if r != param_name
                        ]

        # Process overridable parameters: add as defaults
        if overridable_params and "properties" in parameters:
            for param_name, param_value in overridable_params.items():
                if param_name in parameters["properties"]:
                    parameters["properties"][param_name]["default"] = param_value
                    if "required" in parameters and param_name in parameters["required"]:
                        parameters["required"] = [
                            r for r in parameters["required"] if r != param_name
                        ]

        metadata = {
            "namespace": query.namespace,
            "name": query.name,
            "locked_params": locked_params or {},
            "overridable_params": overridable_params or {},
        }

        # Use query_ prefix for routing in message_service
        llm_tool_name = f"query_{query.namespace}__{query.name}"

        return {
            "type": "function",
            "function": {
                "name": llm_tool_name,
                "description": description,
                "parameters": parameters,
                "_metadata": metadata,
            },
        }

    async def execute_query_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        user_email: Optional[str] = None,
        locked_params: Optional[dict[str, Any]] = None,
        overridable_params: Optional[dict[str, Any]] = None,
        enabled_queries: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Execute a query as a tool call.

        Args:
            db: Database session
            tool_name: Query name as "query_namespace__name"
            arguments: Tool arguments from LLM
            user_id: User ID
            user_email: User email for context injection
            locked_params: Locked parameters
            overridable_params: Overridable parameters
            enabled_queries: List of enabled queries for validation
        """
        # Parse query_namespace__name -> namespace/name
        if tool_name.startswith("query_"):
            tool_name = tool_name[6:]  # Remove "query_" prefix

        if "__" in tool_name and "/" not in tool_name:
            tool_name = tool_name.replace("__", "/", 1)

        if "/" not in tool_name:
            return {"error": f"Invalid query name format: {tool_name}"}

        namespace, name = tool_name.split("/", 1)
        query_ref = f"{namespace}/{name}"

        # SECURITY: Validate query is in enabled list
        if enabled_queries is not None and query_ref not in enabled_queries:
            logger.warning(
                f"Security: LLM attempted to call non-enabled query '{query_ref}'. "
                f"Enabled queries: {enabled_queries}"
            )
            return {
                "error": "Query not enabled",
                "message": f"Query '{query_ref}' is not enabled for this agent.",
            }

        # Load query
        query = await Query.get_by_name(db, namespace, name)
        if not query or not query.is_active:
            return {"error": f"Query not found: {namespace}/{name}"}

        # Check permissions
        user_permissions = await get_user_permissions(db, user_id)
        perm = f"sinas.queries/{namespace}/{name}.execute:own"
        if not check_permission(user_permissions, perm):
            return {
                "error": "Permission denied",
                "message": f"You don't have permission to execute query '{namespace}/{name}'.",
            }

        # Detect locked parameter overrides
        if locked_params and arguments:
            for param_name in locked_params.keys():
                if param_name in arguments:
                    logger.warning(
                        f"Security: LLM attempted to override locked parameter '{param_name}' "
                        f"for query {query_ref}."
                    )

        # Merge parameters: LLM args < overridable < locked
        final_input = {**(arguments or {})}

        if overridable_params:
            for param_name, param_value in overridable_params.items():
                if param_name not in final_input:
                    final_input[param_name] = param_value

        if locked_params:
            final_input.update(locked_params)

        # Inject context variables
        final_input["user_id"] = str(user_id)
        if user_email:
            final_input["user_email"] = user_email

        start_time = time.time()
        try:
            pool_manager = DatabasePoolManager.get_instance()
            result = await pool_manager.execute_query(
                db=db,
                connection_id=str(query.database_connection_id),
                sql=query.sql,
                params=final_input,
                operation=query.operation,
                timeout_ms=query.timeout_ms,
                max_rows=query.max_rows,
            )

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.debug(f"Query execution completed in {elapsed_ms}ms: {query_ref}")

            return result

        except Exception as e:
            logger.error(f"Query execution failed for {query_ref}: {e}")
            return {"error": "Query execution failed", "message": str(e)}
