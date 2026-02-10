"""Anthropic LLM provider implementation."""
from collections.abc import AsyncIterator
from typing import Any, Optional

from anthropic import AsyncAnthropic

from .base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Anthropic (Claude) API provider."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate a completion using Anthropic API."""
        # Convert OpenAI-style messages to Anthropic format
        system_message, filtered_messages = self._convert_messages_to_anthropic(messages)

        params = {
            "model": model,
            "messages": filtered_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 16384,  # Anthropic requires max_tokens
        }

        if system_message:
            params["system"] = system_message

        if tools:
            # Convert OpenAI tool format to Anthropic format
            params["tools"] = self._convert_tools_to_anthropic(tools)

        response = await self.client.messages.create(**params)

        # Extract content (Anthropic returns list of content blocks)
        content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": self._serialize_args(block.input),
                    },
                })

        result = {
            "content": content if content else None,
            "tool_calls": tool_calls if tool_calls else None,
            "usage": self.extract_usage(response),
            "finish_reason": response.stop_reason,
        }

        return result

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        """Generate a streaming completion using Anthropic API."""
        # Convert OpenAI-style messages to Anthropic format
        system_message, filtered_messages = self._convert_messages_to_anthropic(messages)

        params = {
            "model": model,
            "messages": filtered_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 16384,
        }

        if system_message:
            params["system"] = system_message

        if tools:
            params["tools"] = self._convert_tools_to_anthropic(tools)

        # Track tool calls being built across chunks
        current_tool_calls = {}
        current_content = ""

        async with self.client.messages.stream(**params) as stream:
            async for event in stream:
                chunk_data = {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": None,
                }

                if event.type == "content_block_start":
                    if event.content_block.type == "text":
                        pass  # Text will come in content_block_delta
                    elif event.content_block.type == "tool_use":
                        # Start tracking a new tool call
                        idx = event.index
                        current_tool_calls[idx] = {
                            "id": event.content_block.id,
                            "type": "function",
                            "function": {
                                "name": event.content_block.name,
                                "arguments": "",
                            },
                            "index": idx,
                        }

                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        # Text content chunk
                        chunk_data["content"] = event.delta.text
                        current_content += event.delta.text
                    elif hasattr(event.delta, "partial_json"):
                        # Tool call arguments chunk
                        idx = event.index
                        if idx in current_tool_calls:
                            current_tool_calls[idx]["function"]["arguments"] += event.delta.partial_json
                            # Yield tool call deltas
                            chunk_data["tool_calls"] = [current_tool_calls[idx]]

                elif event.type == "message_stop":
                    chunk_data["finish_reason"] = "stop"

                yield chunk_data

    def _convert_messages_to_anthropic(
        self, messages: list[dict[str, Any]]
    ) -> tuple[Optional[str], list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Anthropic format.

        Returns:
            Tuple of (system_message, converted_messages)
        """
        import json

        system_message = None
        converted_messages = []

        for msg in messages:
            role = msg.get("role")

            # Extract system message
            if role == "system":
                system_message = msg["content"]
                continue

            # Convert tool role to user with tool_result content
            if role == "tool":
                converted_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id"),
                            "content": msg.get("content", ""),
                        }
                    ],
                })
                continue

            # Convert assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks = []

                # Add text content if present
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})

                # Add tool_use blocks
                for tool_call in msg["tool_calls"]:
                    function = tool_call.get("function", {})
                    arguments = function.get("arguments", "{}")

                    # Parse arguments if string
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}

                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": function.get("name"),
                        "input": arguments,
                    })

                converted_messages.append({"role": "assistant", "content": content_blocks})
                continue

            # Handle regular user/assistant messages
            if role in ["user", "assistant"]:
                content = msg.get("content")

                # Handle multimodal content (list of content blocks)
                if isinstance(content, list):
                    converted_messages.append({"role": role, "content": content})
                else:
                    # Plain text content
                    converted_messages.append({"role": role, "content": content or ""})
                continue

        return system_message, converted_messages

    def _convert_tools_to_anthropic(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI tool format to Anthropic format."""
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                })
        return anthropic_tools

    def _serialize_args(self, args: Any) -> str:
        """Serialize tool arguments to JSON string."""
        import json
        if isinstance(args, str):
            return args
        return json.dumps(args)

    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        """Format tool calls (already in correct format from complete/stream)."""
        return tool_calls

    def extract_usage(self, response: Any) -> dict[str, int]:
        """Extract token usage from Anthropic response."""
        if hasattr(response, "usage") and response.usage:
            return {
                "prompt_tokens": response.usage.input_tokens or 0,
                "completion_tokens": response.usage.output_tokens or 0,
                "total_tokens": (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0),
            }
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
