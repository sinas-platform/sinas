"""Function-to-tool converter for LLM tool calling."""
import logging
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_user_permissions
from app.core.permissions import check_permission
from app.models.function import Function
from app.services.template_renderer import render_function_parameters

from app.models.execution import TriggerType
from app.services.execution_engine import FunctionExecutionError, executor

logger = logging.getLogger(__name__)


class FunctionToolConverter:
    """Converts functions to OpenAI tool format and manages execution."""

    async def get_available_functions(
        self,
        db: AsyncSession,
        user_id: str,
        enabled_functions: Optional[list[str]] = None,
        disabled_functions: Optional[list[str]] = None,
        function_parameters: Optional[dict[str, dict[str, Any]]] = None,
        agent_input_context: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Get functions and convert to OpenAI tools format.

        Args:
            db: Database session
            user_id: User ID to filter functions
            enabled_functions: List of "namespace/name" strings to include
            disabled_functions: List of "namespace/name" strings to exclude
            function_parameters: Pre-filled parameters with Jinja2 templates
            agent_input_context: Context for rendering Jinja2 templates

        Returns:
            List of tools in OpenAI format with pre-filled parameters
        """
        tools = []

        # If no enabled_functions specified, return empty (opt-in model)
        if not enabled_functions:
            return tools

        for function_ref in enabled_functions:
            # Skip if in disabled list
            if disabled_functions and function_ref in disabled_functions:
                continue

            # Parse namespace/name
            if "/" not in function_ref:
                # Legacy format or invalid - skip
                continue

            namespace, name = function_ref.split("/", 1)

            # Load function by namespace/name (no user filter - permissions checked at execution)
            function = await Function.get_by_name(db, namespace, name)

            if not function or not function.is_active:
                # Skip if function not found or inactive
                continue

            # Get pre-filled parameters for this function (if any)
            prefilled_params = {}
            if function_parameters and function_ref in function_parameters:
                param_templates = function_parameters[function_ref]

                # Render Jinja2 templates using agent input context
                if agent_input_context:
                    try:
                        prefilled_params = render_function_parameters(
                            param_templates, agent_input_context
                        )
                    except Exception as e:
                        # Log error but don't fail - just skip pre-filling

                        logger = logging.getLogger(__name__)
                        logger.warning(
                            f"Failed to render function parameters for {function_ref}: {e}"
                        )
                else:
                    # No context - use raw values (non-template strings pass through)
                    prefilled_params = param_templates

            # Convert to OpenAI tool format
            tool = self._function_to_tool(function, prefilled_params)
            tools.append(tool)

        return tools

    def _function_to_tool(
        self, function: Function, prefilled_params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """
        Convert a function to OpenAI tool format.

        Args:
            function: Function model with input_schema
            prefilled_params: Pre-filled parameter values (already rendered)

        Returns:
            OpenAI tool dict
        """
        # Build description
        description = function.description or f"Execute {function.namespace}/{function.name}"

        # Use function's input_schema
        parameters = function.input_schema.copy() if function.input_schema else {"type": "object"}

        # Mark pre-filled parameters as optional and add defaults
        if prefilled_params and "properties" in parameters:
            for param_name, param_value in prefilled_params.items():
                if param_name in parameters["properties"]:
                    # Add default value
                    parameters["properties"][param_name]["default"] = param_value

                    # Remove from required list (since we have default)
                    if "required" in parameters and param_name in parameters["required"]:
                        parameters["required"] = [
                            r for r in parameters["required"] if r != param_name
                        ]

        # Store prefilled params in metadata for later use
        metadata = {
            "namespace": function.namespace,
            "name": function.name,
            "prefilled_params": prefilled_params or {},
        }

        # Convert namespace/name to namespace__name for LLM compatibility
        # OpenAI function names cannot contain "/" (must match ^[a-zA-Z0-9_-]{1,64}$)
        llm_tool_name = f"{function.namespace}__{function.name}"

        return {
            "type": "function",
            "function": {
                "name": llm_tool_name,
                "description": description,
                "parameters": parameters,
                "_metadata": metadata,  # Internal use for execution
            },
        }

    async def execute_function_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        user_token: str,
        chat_id: Optional[str] = None,
        prefilled_params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute a function as a tool call.

        Args:
            db: Database session
            tool_name: Function name as "namespace__name" or "namespace/name"
            arguments: Tool arguments from LLM
            user_id: User ID
            user_token: User's JWT or API key for authentication
            chat_id: Optional chat ID for context
            prefilled_params: Pre-filled parameters to merge with arguments

        Returns:
            Tool execution result

        Raises:
            ValueError: If function not found
        """
        # Convert namespace__name to namespace/name if needed
        # (OpenAI-compatible APIs don't allow "/" in function names)
        if "__" in tool_name and "/" not in tool_name:
            tool_name = tool_name.replace("__", "/", 1)

        # Parse namespace/name
        if "/" not in tool_name:
            raise ValueError(f"Invalid function name format: {tool_name}")

        namespace, name = tool_name.split("/", 1)

        # Load function
        function = await Function.get_by_name(db, namespace, name)

        if not function or not function.is_active:
            raise ValueError(f"Function not found: {tool_name}")

        # Check permissions: sinas.functions/{namespace}/{name}.execute:own or :all
        user_permissions = await get_user_permissions(db, user_id)
        execute_perm_all = f"sinas.functions/{namespace}/{name}.execute:all"
        execute_perm_own = f"sinas.functions/{namespace}/{name}.execute:own"

        has_permission = check_permission(user_permissions, execute_perm_all) or (
            check_permission(user_permissions, execute_perm_own)
            and str(function.user_id) == user_id
        )

        if not has_permission:
            return {
                "error": "Permission denied",
                "message": f"You don't have permission to execute function '{namespace}/{name}'. Required: sinas.functions/{namespace}/{name}.execute:own or :all",
            }

        # Merge prefilled params with LLM arguments
        # Prefilled params take precedence (handle None arguments as empty dict)
        final_input = {**(arguments or {}), **(prefilled_params or {})}


        # Generate execution ID
        execution_id = str(uuid.uuid4())

        try:
            result = await executor.execute_function(
                function_namespace=namespace,
                function_name=name,
                input_data=final_input,
                execution_id=execution_id,
                trigger_type=TriggerType.AGENT.value,
                trigger_id=chat_id or "unknown",
                user_id=user_id,
                chat_id=chat_id,
            )

            # Return execution result (raw value on success)
            logger.debug(f"Function execution succeeded: {result}")
            return result

        except FunctionExecutionError as e:
            # Function execution failed - return error
            logger.error(f"Function execution failed: {e}")
            return {"error": "Function execution failed", "message": str(e)}
