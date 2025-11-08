"""Webhook-to-tool converter for LLM tool calling."""
import json
from typing import List, Dict, Any, Optional
import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Webhook, Function


class WebhookToolConverter:
    """Converts webhooks to OpenAI tool format and executes them."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """
        Initialize converter.

        Args:
            base_url: Base URL for webhook execution
        """
        self.base_url = base_url.rstrip("/")

    async def get_available_webhooks(
        self,
        db: AsyncSession,
        user_id: str,
        enabled_webhooks: Optional[List[str]] = None,
        disabled_webhooks: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get webhooks and convert to OpenAI tools format.

        Args:
            db: Database session
            user_id: User ID to filter webhooks
            enabled_webhooks: If provided, only include these webhooks
            disabled_webhooks: If provided, exclude these webhooks

        Returns:
            List of tools in OpenAI format
        """
        # Query active webhooks for user (or shared via group)
        query = select(Webhook).where(
            Webhook.is_active == True,
            Webhook.user_id == user_id
        )

        result = await db.execute(query)
        webhooks = result.scalars().all()

        tools = []

        for webhook in webhooks:
            # Apply filtering
            if enabled_webhooks is not None and webhook.function_name not in enabled_webhooks:
                continue

            if disabled_webhooks is not None and webhook.function_name in disabled_webhooks:
                continue

            # Look up linked function to get schema
            func_result = await db.execute(
                select(Function).where(
                    Function.name == webhook.function_name,
                    Function.user_id == user_id
                )
            )
            function = func_result.scalar_one_or_none()

            if not function:
                # Skip if function not found
                continue

            # Convert to OpenAI tool format
            tool = self._webhook_to_tool(webhook, function)
            tools.append(tool)

        return tools

    def _webhook_to_tool(self, webhook: Webhook, function: Function) -> Dict[str, Any]:
        """
        Convert a webhook + function to OpenAI tool format.

        Args:
            webhook: Webhook model
            function: Function model with input_schema

        Returns:
            OpenAI tool dict
        """
        # Build description
        description = webhook.description or function.description or f"Execute {webhook.function_name}"
        description += f" (Webhook: {webhook.http_method} /api/v1/h/{webhook.path})"

        # Extract parameters from function's input_schema
        parameters = function.input_schema

        return {
            "type": "function",
            "function": {
                "name": webhook.function_name,
                "description": description,
                "parameters": parameters
            }
        }

    async def execute_webhook_tool(
        self,
        db: AsyncSession,
        tool_name: str,
        arguments: Dict[str, Any],
        user_token: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a webhook as a tool call.

        Args:
            db: Database session
            tool_name: Function name (used to find webhook)
            arguments: Tool arguments
            user_token: User's JWT or API key for authentication

        Returns:
            Tool execution result

        Raises:
            ValueError: If webhook not found
            httpx.HTTPError: If webhook execution fails
        """
        # Find webhook by function_name
        result = await db.execute(
            select(Webhook).where(
                Webhook.function_name == tool_name,
                Webhook.is_active == True
            )
        )
        webhook = result.scalar_one_or_none()

        if not webhook:
            raise ValueError(f"Webhook not found for tool: {tool_name}")

        # Construct webhook URL
        webhook_url = f"{self.base_url}/api/v1/h/{webhook.path}"

        # Execute webhook via HTTP
        async with httpx.AsyncClient(timeout=300.0) as client:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {user_token}"
            }

            # Add chat_id header if provided
            if chat_id:
                headers["X-Chat-ID"] = chat_id

            # Merge default values with arguments
            payload = {**(webhook.default_values or {}), **arguments}

            # Make request with appropriate HTTP method
            if webhook.http_method == "POST":
                response = await client.post(webhook_url, json=payload, headers=headers)
            elif webhook.http_method == "GET":
                response = await client.get(webhook_url, params=payload, headers=headers)
            elif webhook.http_method == "PUT":
                response = await client.put(webhook_url, json=payload, headers=headers)
            elif webhook.http_method == "DELETE":
                response = await client.delete(webhook_url, json=payload, headers=headers)
            elif webhook.http_method == "PATCH":
                response = await client.patch(webhook_url, json=payload, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {webhook.http_method}")

            # Handle response
            if response.status_code >= 400:
                return {
                    "error": f"Webhook execution failed: {response.status_code}",
                    "details": response.text
                }

            try:
                return response.json()
            except json.JSONDecodeError:
                return {"result": response.text}

    async def get_tool_configuration(
        self,
        db: AsyncSession,
        chat_enabled: Optional[List[str]] = None,
        assistant_enabled: Optional[List[str]] = None,
        message_enabled: Optional[List[str]] = None,
        message_disabled: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Determine final tool configuration based on hierarchy.

        Hierarchy: Message > Chat > Assistant > All

        Args:
            db: Database session
            chat_enabled: Enabled webhooks from chat
            assistant_enabled: Enabled webhooks from assistant
            message_enabled: Enabled webhooks from message (override)
            message_disabled: Disabled webhooks from message

        Returns:
            Dict with 'enabled' and 'disabled' lists
        """
        # Start with message-level overrides if provided
        if message_enabled is not None:
            enabled = message_enabled
        elif chat_enabled is not None:
            enabled = chat_enabled
        elif assistant_enabled is not None:
            enabled = assistant_enabled
        else:
            enabled = None  # None means "all"

        disabled = message_disabled or []

        return {
            "enabled": enabled,
            "disabled": disabled
        }
