"""Base LLM provider interface."""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Optional


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    # Whether this provider implements the batch API methods below
    # (submit_batch / get_batch_status / fetch_batch_results / cancel_batch).
    supports_batch: bool = False

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """
        Initialize the provider.

        Args:
            api_key: API key for the provider
            base_url: Base URL for API endpoints
        """
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Generate a completion from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model identifier
            tools: Optional list of tools in OpenAI format
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Returns:
            Dict with completion response including:
                - content: The generated text
                - tool_calls: List of tool calls if any
                - usage: Token usage statistics
                - finish_reason: Why generation stopped
        """
        pass

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Generate a streaming completion from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model identifier
            tools: Optional list of tools in OpenAI format
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Yields:
            Dict chunks with incremental completion data. Chunks carry
            'content', 'tool_calls' and 'finish_reason' keys. A chunk near
            the end of the stream (typically the last one) additionally
            carries a 'usage' key with the same shape as extract_usage();
            consumers must treat 'usage' as optional per chunk.
        """
        pass

    @abstractmethod
    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        """
        Convert provider-specific tool call format to standard format.

        Args:
            tool_calls: Provider-specific tool call data

        Returns:
            List of tool calls in standard format:
            [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "function_name",
                        "arguments": "{...}"
                    }
                }
            ]
        """
        pass

    @abstractmethod
    def extract_usage(self, response: Any) -> dict[str, int]:
        """
        Extract token usage from provider response.

        Args:
            response: Provider response object

        Returns:
            Dict with usage statistics:
            {
                "prompt_tokens": int,
                "completion_tokens": int,
                "total_tokens": int
            }
        """
        pass

    # ── Provider batch API (opt-in; see supports_batch) ──────────────────
    #
    # Request item shape (input to submit_batch):
    #   {"custom_id": str, "messages": [...openai-style...], "model": str,
    #    "temperature": float, "max_tokens": Optional[int]}
    # Result item shape (output of fetch_batch_results):
    #   {"custom_id": str, "status": "succeeded"|"errored"|"cancelled"|"expired",
    #    "content": Optional[str], "usage": Optional[dict], "error": Optional[str]}

    async def submit_batch(self, requests: list[dict[str, Any]]) -> str:
        """Submit requests to the provider's batch API; returns the provider batch id."""
        raise NotImplementedError(f"{type(self).__name__} does not support batch submission")

    async def get_batch_status(self, provider_batch_id: str) -> dict[str, Any]:
        """Return {"status": <raw provider status>, "ended": bool}."""
        raise NotImplementedError(f"{type(self).__name__} does not support batch submission")

    async def fetch_batch_results(self, provider_batch_id: str) -> list[dict[str, Any]]:
        """Return one result item per request (see shape above). Only valid once ended."""
        raise NotImplementedError(f"{type(self).__name__} does not support batch submission")

    async def cancel_batch(self, provider_batch_id: str) -> None:
        """Best-effort cancellation of an in-flight provider batch."""
        raise NotImplementedError(f"{type(self).__name__} does not support batch submission")
