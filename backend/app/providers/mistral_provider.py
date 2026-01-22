"""Mistral AI LLM provider implementation."""
import json
from typing import List, Dict, Any, Optional, AsyncIterator
from openai import AsyncOpenAI

from .base import BaseLLMProvider


class MistralProvider(BaseLLMProvider):
    """
    Mistral AI provider using OpenAI-compatible API.

    Mistral supports the OpenAI chat completions format with additional features:
    - Vision (image_url chunks)
    - Audio (input_audio chunks)
    - Documents (document_url chunks)
    - Function calling
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        # Use OpenAI client with Mistral endpoint
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.mistral.ai/v1"
        )

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate a completion using Mistral API."""
        params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens:
            params["max_tokens"] = max_tokens

        if tools:
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")

        # Add any additional kwargs (Mistral-specific params)
        for key in ["top_p", "random_seed", "safe_prompt", "response_format"]:
            if key in kwargs:
                params[key] = kwargs[key]

        response = await self.client.chat.completions.create(**params)

        message = response.choices[0].message

        result = {
            "content": message.content or "",
            "tool_calls": None,
            "usage": self.extract_usage(response),
            "finish_reason": response.choices[0].finish_reason,
        }

        if message.tool_calls:
            result["tool_calls"] = self.format_tool_calls(message.tool_calls)

        return result

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
        """Generate a streaming completion using Mistral API."""
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
            params["tool_choice"] = kwargs.get("tool_choice", "auto")

        # Add any additional kwargs
        for key in ["top_p", "random_seed", "safe_prompt", "response_format"]:
            if key in kwargs:
                params[key] = kwargs[key]

        stream = await self.client.chat.completions.create(**params)

        # Collect tool calls across chunks
        tool_calls_buffer = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            chunk_data = {
                "content": delta.content if delta.content else None,
                "tool_calls": None,
                "finish_reason": chunk.choices[0].finish_reason,
            }

            if delta.tool_calls:
                # Buffer tool calls
                for tool_call in delta.tool_calls:
                    idx = tool_call.index
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {
                            "id": tool_call.id or "",
                            "type": "function",
                            "function": {
                                "name": "",
                                "arguments": ""
                            }
                        }

                    if tool_call.id:
                        tool_calls_buffer[idx]["id"] = tool_call.id

                    if tool_call.function:
                        if tool_call.function.name:
                            tool_calls_buffer[idx]["function"]["name"] = tool_call.function.name
                        if tool_call.function.arguments:
                            tool_calls_buffer[idx]["function"]["arguments"] += tool_call.function.arguments

            # When streaming finishes, include complete tool calls
            if chunk_data["finish_reason"] and tool_calls_buffer:
                chunk_data["tool_calls"] = list(tool_calls_buffer.values())

            yield chunk_data

    def format_tool_calls(self, tool_calls: Any) -> List[Dict[str, Any]]:
        """Convert Mistral/OpenAI tool call format to standard format."""
        result = []
        for tool_call in tool_calls:
            result.append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
        return result

    def extract_usage(self, response: Any) -> Dict[str, int]:
        """Extract token usage from Mistral response."""
        if hasattr(response, "usage") and response.usage:
            return {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
                "total_tokens": response.usage.total_tokens or 0
            }
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
