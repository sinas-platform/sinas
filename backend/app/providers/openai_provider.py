"""OpenAI LLM provider implementation."""
from collections.abc import AsyncIterator
from typing import Any, Optional

from openai import AsyncOpenAI

from .base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate a completion using OpenAI API."""
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens:
            params["max_tokens"] = max_tokens

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # Add any additional kwargs
        params.update(kwargs)

        response = await self.client.chat.completions.create(**params)

        message = response.choices[0].message

        result = {
            "content": message.content,
            "tool_calls": None,
            "usage": self.extract_usage(response),
            "finish_reason": response.choices[0].finish_reason,
        }

        if message.tool_calls:
            result["tool_calls"] = self.format_tool_calls(message.tool_calls)

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
        """Generate a streaming completion using OpenAI API."""
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if max_tokens:
            params["max_tokens"] = max_tokens

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # Add any additional kwargs
        params.update(kwargs)

        stream = await self.client.chat.completions.create(**params)

        async for chunk in stream:
            delta = chunk.choices[0].delta

            result = {
                "content": delta.content if delta.content else None,
                "tool_calls": None,
                "finish_reason": chunk.choices[0].finish_reason,
            }

            if delta.tool_calls:
                result["tool_calls"] = self.format_tool_calls(delta.tool_calls)

            yield result

    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        """Convert OpenAI tool calls to standard format (already in correct format)."""
        formatted = []

        for idx, tc in enumerate(tool_calls):
            # Get ID or generate fallback
            call_id = tc.id if hasattr(tc, "id") and tc.id else f"call_{idx}"

            tool_call_dict = {
                "id": call_id,
                "type": tc.type if hasattr(tc, "type") else "function",
                "function": {
                    "name": tc.function.name if hasattr(tc.function, "name") else None,
                    "arguments": tc.function.arguments
                    if hasattr(tc.function, "arguments")
                    else None,
                },
            }
            # Include index if present (used in streaming)
            if hasattr(tc, "index"):
                tool_call_dict["index"] = tc.index

            formatted.append(tool_call_dict)

        return formatted

    def extract_usage(self, response: Any) -> dict[str, int]:
        """Extract token usage from OpenAI response."""
        if not response.usage:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        return {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
