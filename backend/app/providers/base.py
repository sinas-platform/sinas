"""Base LLM provider interface."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

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
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
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
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[Dict[str, Any]]:
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
            Dict chunks with incremental completion data
        """
        pass

    @abstractmethod
    def format_tool_calls(self, tool_calls: Any) -> List[Dict[str, Any]]:
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
    def extract_usage(self, response: Any) -> Dict[str, int]:
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
