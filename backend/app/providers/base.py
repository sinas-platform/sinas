"""Base LLM provider interface."""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Optional


def reasoning_text(delta: Any) -> Optional[str]:
    """The thinking in an OpenAI-style stream delta, if the server sends it.

    OpenAI's own API never streams reasoning, but most OpenAI-compatible
    servers do, as an extra field: `reasoning_content` (DeepSeek, vLLM,
    LM Studio, Gemini) or `reasoning` (OpenRouter, Groq, Ollama's /v1).
    """
    for field in ("reasoning_content", "reasoning"):
        value = delta.get(field) if isinstance(delta, dict) else getattr(delta, field, None)
        if isinstance(value, str) and value:
            return value
    return None


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
        # Whether calls carry the current W3C trace context and baggage
        # (`traceparent`, `baggage`) to the model API. Off by default: the
        # baggage says whom a request is for, which a public model API has no
        # use for; on for a gateway that records it, such as a LiteLLM proxy.
        # Set from the provider's config (`propagate_context`).
        self.propagate_context = False
        # The provider's header templates, rendered for this call's identity
        # (app/providers/attribution.py). Sent on every request, over any
        # other header of the same name.
        self.identity_headers: dict[str, str] = {}
        # The provider's own budget refusal rules (`config.budget_errors`,
        # app/providers/errors.py), for the SDK client's no-retry hook.
        self.budget_rules: list[dict[str, Any]] = []
        # The run's own credential for this provider, when the chat (or the
        # request) has one (app/providers/run_credentials.py). Sent by
        # reference: see `_run_credential_headers`.
        self.run_credential: Optional[Any] = None

    def _run_credential_headers(self) -> dict[str, str]:
        """The run credential's reference (a private header the client's hook
        takes off) and a placeholder in each header it goes in, which the
        hook replaces with the value (`clients._apply_run_credential`)."""
        if self.run_credential is None:
            return {}
        from .clients import RUN_CREDENTIAL_HEADER

        return {**self.run_credential.placeholders(), RUN_CREDENTIAL_HEADER: self.run_credential.ref}

    def scrub(self, text: str) -> str:
        """`text` (an error body, a log line) without the run credential."""
        if self.run_credential is None or not text:
            return text
        from .errors import scrub

        return scrub(text, self.run_credential.secrets())

    def _context_headers(self) -> dict[str, str]:
        """The headers that carry the current trace context, when enabled."""
        if not self.propagate_context:
            return {}
        from app.core.telemetry import inject_trace_context

        return inject_trace_context()

    def _sdk_context_headers(self) -> dict[str, str]:
        """`_context_headers`, for a call through an SDK client: marked, so
        that the client's guard (`clients.guard_trace_context`) lets them
        through. Without the mark it drops any trace context the SDK adds on
        its own."""
        headers = self._context_headers()
        if not headers:
            return {}
        from .clients import CONTEXT_OPT_IN_HEADER

        return {**headers, CONTEXT_OPT_IN_HEADER: "1"}

    def _request_headers(self) -> dict[str, str]:
        """Every header Sinas adds to a request sent over plain HTTP (not
        through an SDK client, but through a client guarded the same way):
        the trace context, when enabled and marked for the guard, the
        identity headers and the run credential (by reference)."""
        from .attribution import merge_headers

        return merge_headers(
            self._sdk_context_headers(), self.identity_headers, self._run_credential_headers()
        )

    def _sdk_request_headers(self) -> dict[str, str]:
        """`_request_headers` for a call through an SDK client: the trace
        context marked for the client's guard, the budget rules for its
        no-retry hook (private headers the hooks take off again), the
        identity headers, and the run credential by reference."""
        from .attribution import merge_headers

        private: dict[str, str] = {}
        if self.budget_rules:
            import json

            from .clients import BUDGET_RULES_HEADER

            private[BUDGET_RULES_HEADER] = json.dumps(self.budget_rules, separators=(",", ":"))
        return merge_headers(
            self._sdk_context_headers(), private, self.identity_headers, self._run_credential_headers()
        )

    def _add_context_headers(self, call_kwargs: dict[str, Any]) -> None:
        """Add Sinas's headers (`_sdk_request_headers`) to an SDK call's
        `extra_headers`, in place, over any of the same name already there
        (from `extra_params`, say).

        For the keyword arguments of the call that sends a request, never for
        a request body: they travel as HTTP headers, and a body that is sent
        as data (a batch item) would carry them as unknown fields.
        """
        headers = self._sdk_request_headers()
        if headers:
            from .attribution import merge_headers

            call_kwargs["extra_headers"] = merge_headers(call_kwargs.get("extra_headers"), headers)

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
    #    "content": Optional[str], "usage": Optional[dict], "error": Optional[str],
    #    "finish_reason": Optional[str]}  (the provider's own; when it reports one)

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
