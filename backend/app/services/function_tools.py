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
            # Parse locked vs overridable parameters
            locked_params = {}
            overridable_params = {}

            if function_parameters and function_ref in function_parameters:
                param_templates = function_parameters[function_ref]

                # Separate locked vs overridable parameters
                for param_name, param_config in param_templates.items():
                    # Check if using new object format: {"value": ..., "locked": true/false}
                    if isinstance(param_config, dict) and "value" in param_config:
                        param_value = param_config["value"]
                        is_locked = param_config.get("locked", False)

                        if is_locked:
                            locked_params[param_name] = param_value
                        else:
                            overridable_params[param_name] = param_value
                    else:
                        # Legacy format: treat as overridable default
                        overridable_params[param_name] = param_config

                # Render Jinja2 templates using agent input context
                if agent_input_context:
                    try:
                        locked_params = render_function_parameters(
                            locked_params, agent_input_context
                        )
                        overridable_params = render_function_parameters(
                            overridable_params, agent_input_context
                        )
                    except Exception as e:
                        # Log error but don't fail - just skip pre-filling
                        logger.warning(
                            f"Failed to render function parameters for {function_ref}: {e}"
                        )
                        locked_params = {}
                        overridable_params = {}

            # Convert to OpenAI tool format
            tool = self._function_to_tool(function, locked_params, overridable_params)
            tools.append(tool)

        return tools

    def _function_to_tool(
        self,
        function: Function,
        locked_params: Optional[dict[str, Any]] = None,
        overridable_params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Convert a function to OpenAI tool format.

        Args:
            function: Function model with input_schema
            locked_params: Locked parameter values (hidden from LLM, always applied)
            overridable_params: Overridable parameter defaults (shown to LLM as defaults)

        Returns:
            OpenAI tool dict
        """
        # Build description
        description = function.description or f"Execute {function.namespace}/{function.name}"

        # Use function's input_schema
        parameters = function.input_schema.copy() if function.input_schema else {"type": "object"}

        # Process locked parameters: hide from schema entirely
        if locked_params and "properties" in parameters:
            for param_name in locked_params.keys():
                if param_name in parameters["properties"]:
                    # Remove from properties (LLM won't see it)
                    del parameters["properties"][param_name]

                    # Remove from required list
                    if "required" in parameters and param_name in parameters["required"]:
                        parameters["required"] = [
                            r for r in parameters["required"] if r != param_name
                        ]

        # Process overridable parameters: add as defaults
        if overridable_params and "properties" in parameters:
            for param_name, param_value in overridable_params.items():
                if param_name in parameters["properties"]:
                    # Add default value (LLM can see and override)
                    parameters["properties"][param_name]["default"] = param_value

                    # Remove from required list (since we have default)
                    if "required" in parameters and param_name in parameters["required"]:
                        parameters["required"] = [
                            r for r in parameters["required"] if r != param_name
                        ]

        # Store params in metadata for later use during execution
        metadata = {
            "namespace": function.namespace,
            "name": function.name,
            "locked_params": locked_params or {},
            "overridable_params": overridable_params or {},
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
        locked_params: Optional[dict[str, Any]] = None,
        overridable_params: Optional[dict[str, Any]] = None,
        enabled_functions: Optional[list[str]] = None,
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
            locked_params: Locked parameters (always applied, LLM cannot override)
            overridable_params: Overridable parameters (applied as defaults, LLM can override)
            enabled_functions: List of enabled functions for validation

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
        function_ref = f"{namespace}/{name}"

        # SECURITY: Validate function is in enabled list
        if enabled_functions is not None and function_ref not in enabled_functions:
            logger.warning(
                f"Security: LLM attempted to call non-enabled function '{function_ref}'. "
                f"Enabled functions: {enabled_functions}"
            )
            return {
                "error": "Function not enabled",
                "message": f"Function '{function_ref}' is not enabled for this agent. Only enabled functions can be called.",
            }

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

        # SECURITY: Detect if LLM tried to override locked parameters
        if locked_params and arguments:
            for param_name in locked_params.keys():
                if param_name in arguments:
                    logger.warning(
                        f"Security: LLM attempted to override locked parameter '{param_name}' "
                        f"for function {function_ref}. "
                        f"Locked value: {locked_params[param_name]}, "
                        f"LLM value: {arguments[param_name]}"
                    )

        # Merge parameters with proper precedence:
        # 1. Start with LLM arguments (lowest priority)
        # 2. Apply overridable params as defaults (medium priority)
        # 3. Apply locked params (highest priority - cannot be overridden)
        final_input = {**(arguments or {})}

        if overridable_params:
            # Apply overridable params only if not already set by LLM
            for param_name, param_value in overridable_params.items():
                if param_name not in final_input:
                    final_input[param_name] = param_value

        if locked_params:
            # Apply locked params unconditionally (override anything LLM provided)
            final_input.update(locked_params)

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
