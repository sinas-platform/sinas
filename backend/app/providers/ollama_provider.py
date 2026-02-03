"""Ollama LLM provider implementation."""
import json
from collections.abc import AsyncIterator
from typing import Any, Optional

import httpx

from .base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider."""

    def __init__(
        self, api_key: Optional[str] = None, base_url: Optional[str] = "http://localhost:11434"
    ):
        super().__init__(api_key, base_url)
        self.base_url = base_url or "http://localhost:11434"

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate a completion using Ollama API."""
        async with httpx.AsyncClient(timeout=300.0) as client:
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature,
                },
            }

            if max_tokens:
                payload["options"]["num_predict"] = max_tokens

            # Ollama supports tools in recent versions
            if tools:
                payload["tools"] = self._convert_tools_to_ollama_format(tools)

            response = await client.post(f"{self.base_url}/api/chat", json=payload)

            # Debug logging
            if response.status_code != 200:
                import logging

                logger = logging.getLogger(__name__)
                logger.error(f"Ollama API error: {response.status_code}")
                logger.error(f"URL: {self.base_url}/api/chat")
                logger.error(f"Payload: {payload}")
                logger.error(f"Response: {response.text}")

            response.raise_for_status()
            data = response.json()

            message = data.get("message", {})
            content = message.get("content", "")
            tool_calls_data = message.get("tool_calls")

            result = {
                "content": content,
                "tool_calls": None,
                "usage": self.extract_usage(data),
                "finish_reason": "stop",  # Ollama doesn't always provide this
            }

            if tool_calls_data:
                result["tool_calls"] = self.format_tool_calls(tool_calls_data)

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
        """Generate a streaming completion using Ollama API."""
        async with httpx.AsyncClient(timeout=300.0) as client:
            payload = {
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": temperature,
                },
            }

            if max_tokens:
                payload["options"]["num_predict"] = max_tokens

            if tools:
                payload["tools"] = self._convert_tools_to_ollama_format(tools)

            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    try:
                        data = json.loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        tool_calls_data = message.get("tool_calls")
                        done = data.get("done", False)

                        result = {
                            "content": content if content else None,
                            "tool_calls": None,
                            "finish_reason": "stop" if done else None,
                        }

                        if tool_calls_data:
                            result["tool_calls"] = self.format_tool_calls(tool_calls_data)

                        yield result

                    except json.JSONDecodeError:
                        continue

    def _convert_tools_to_ollama_format(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Convert OpenAI tool format to Ollama tool format.

        Ollama uses a similar format to OpenAI, but may have slight differences.
        """
        ollama_tools = []

        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                ollama_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": func.get("name"),
                            "description": func.get("description", ""),
                            "parameters": func.get("parameters", {}),
                        },
                    }
                )

        return ollama_tools

    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        """
        Convert Ollama tool calls to standard OpenAI format.

        Ollama format may vary, but typically similar to:
        {
            "function": {
                "name": "function_name",
                "arguments": {...}
            }
        }
        """
        formatted = []

        if isinstance(tool_calls, list):
            for idx, tc in enumerate(tool_calls):
                func = tc.get("function", {})

                # Generate ID if not provided
                call_id = tc.get("id", f"call_{idx}")

                # Handle arguments - may be dict or string
                arguments = func.get("arguments", {})
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments)

                formatted.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": func.get("name", ""), "arguments": arguments},
                    }
                )

        return formatted

    def extract_usage(self, response: Any) -> dict[str, int]:
        """
        Extract token usage from Ollama response.

        Ollama provides eval_count (output tokens) and prompt_eval_count (input tokens).
        """
        prompt_tokens = response.get("prompt_eval_count", 0)
        completion_tokens = response.get("eval_count", 0)

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
