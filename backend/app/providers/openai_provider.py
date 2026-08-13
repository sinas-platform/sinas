"""OpenAI LLM provider implementation."""
import json
from collections.abc import AsyncIterator
from typing import Any, Optional

from openai import AsyncOpenAI

from .base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    supports_batch = True

    """OpenAI API provider."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _prepare_params(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: Optional[int],
        tools: Optional[list[dict[str, Any]]],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build the chat-completions request payload.

        Extracted as a hook so subclasses (e.g. Azure) can adjust parameter
        names or strip params unsupported by certain deployments without
        re-implementing complete()/stream().
        """
        params: dict[str, Any] = {
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

        return params

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
        params = self._prepare_params(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            kwargs=kwargs,
        )

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
        # Ask for token usage on the final chunk. Routed through kwargs so
        # subclasses can strip it via their param handling (e.g. Azure
        # drop_params) for gateways that reject stream_options.
        kwargs.setdefault("stream_options", {"include_usage": True})
        params = self._prepare_params(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            kwargs=kwargs,
        )
        params["stream"] = True

        stream = await self.client.chat.completions.create(**params)

        async for chunk in stream:
            usage = self.extract_usage(chunk) if getattr(chunk, "usage", None) else None

            if not chunk.choices:
                # With include_usage the final chunk has no choices, only usage
                if usage:
                    yield {
                        "content": None,
                        "tool_calls": None,
                        "finish_reason": None,
                        "usage": usage,
                    }
                continue

            delta = chunk.choices[0].delta

            result = {
                "content": delta.content if delta.content else None,
                "tool_calls": None,
                "finish_reason": chunk.choices[0].finish_reason,
            }

            if usage:
                result["usage"] = usage

            if delta.tool_calls:
                result["tool_calls"] = self.format_tool_calls(delta.tool_calls)

            yield result

    def format_tool_calls(self, tool_calls: Any) -> list[dict[str, Any]]:
        """Convert OpenAI tool calls to standard format (already in correct format).

        Extra fields the endpoint attaches are preserved, not reconstructed
        away: OpenAI-compatible gateways can require them round-tripped in
        the conversation history. Gemini 3 rejects tool-loop turns with
        "Function call is missing a thought_signature" unless the signature
        from each functionCall part is sent back verbatim.
        """
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

            # Preserve extra fields the SDK didn't model (pydantic stores
            # unknown response fields in model_extra) — e.g. Gemini's
            # thought_signature / extra_content on the call or its function.
            for key, value in (getattr(tc, "model_extra", None) or {}).items():
                if value is not None and key not in tool_call_dict:
                    tool_call_dict[key] = value
            fn = getattr(tc, "function", None)
            for key, value in (getattr(fn, "model_extra", None) or {}).items():
                if value is not None and key not in tool_call_dict["function"]:
                    tool_call_dict["function"][key] = value

            formatted.append(tool_call_dict)

        return formatted

    def extract_usage(self, response: Any) -> dict[str, int]:
        """Extract token usage from OpenAI response.

        OpenAI's automatic prompt caching reports the cached portion in
        prompt_tokens_details.cached_tokens (already included in
        prompt_tokens). Also covers OpenAI-compatible endpoints (Azure
        inherits this; Gemini's compat layer may report it too).
        cache_write_tokens stays 0: OpenAI has no write premium.
        """
        if not response.usage:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }

        details = getattr(response.usage, "prompt_tokens_details", None)
        cache_read = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": 0,
        }

    # ── Batch API ─────────────────────────────────────────────────────────

    async def _upload_batch_file(self, jsonl: bytes) -> str:
        """Upload the batch input file; returns its file id.

        Hook: Gemini's OpenAI-compat endpoint supports batches.create but not
        files.create — its subclass replaces this with Google's Files API.
        """
        input_file = await self.client.files.create(
            file=("batch.jsonl", jsonl), purpose="batch"
        )
        return input_file.id

    async def _download_batch_file(self, file_id: str) -> str:
        """Download a batch output/error file's text content (same hook rationale)."""
        content = await self.client.files.content(file_id)
        return content.text

    async def submit_batch(self, requests: list[dict[str, Any]]) -> str:
        lines = []
        for req in requests:
            body = self._prepare_params(
                model=req["model"],
                messages=req["messages"],
                temperature=req.get("temperature", 0.7),
                max_tokens=req.get("max_tokens"),
                tools=None,
                kwargs={},
            )
            lines.append(json.dumps({
                "custom_id": req["custom_id"],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }))
        jsonl = ("\n".join(lines) + "\n").encode("utf-8")

        input_file_id = await self._upload_batch_file(jsonl)
        batch = await self.client.batches.create(
            input_file_id=input_file_id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        return batch.id

    async def get_batch_status(self, provider_batch_id: str) -> dict[str, Any]:
        batch = await self.client.batches.retrieve(provider_batch_id)
        return {
            "status": batch.status,
            "ended": batch.status in ("completed", "failed", "expired", "cancelled"),
        }

    async def fetch_batch_results(self, provider_batch_id: str) -> list[dict[str, Any]]:
        batch = await self.client.batches.retrieve(provider_batch_id)
        results: list[dict[str, Any]] = []
        # A cancelled/expired batch still exposes results for the completed
        # portion via output_file_id; the rest surfaces via error_file_id or
        # is simply absent (the poller fails leftovers explicitly).
        expired = batch.status == "expired"

        for file_id, is_error_file in (
            (batch.output_file_id, False),
            (batch.error_file_id, True),
        ):
            if not file_id:
                continue
            text = await self._download_batch_file(file_id)
            for line in text.splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                item: dict[str, Any] = {
                    "custom_id": entry.get("custom_id"),
                    "status": "errored" if is_error_file else "succeeded",
                    "content": None,
                    "usage": None,
                    "error": None,
                }
                response = entry.get("response") or {}
                body = response.get("body") or {}
                if not is_error_file and response.get("status_code") == 200:
                    choices = body.get("choices") or []
                    if choices:
                        item["content"] = (choices[0].get("message") or {}).get("content")
                    usage = body.get("usage") or {}
                    # Gemini's batch output reports usage in camelCase
                    # (promptTokens); OpenAI uses snake_case. Accept both.
                    details = (
                        usage.get("prompt_tokens_details")
                        or usage.get("promptTokensDetails")
                        or {}
                    )
                    item["usage"] = {
                        "prompt_tokens": usage.get("prompt_tokens")
                        or usage.get("promptTokens") or 0,
                        "completion_tokens": usage.get("completion_tokens")
                        or usage.get("completionTokens") or 0,
                        "total_tokens": usage.get("total_tokens")
                        or usage.get("totalTokens") or 0,
                        "cache_read_tokens": details.get("cached_tokens")
                        or details.get("cachedTokens") or 0,
                        "cache_write_tokens": 0,
                    }
                else:
                    item["status"] = "expired" if expired else "errored"
                    error = entry.get("error") or body.get("error") or response.get("status_code")
                    item["error"] = "provider_batch_expired" if expired else str(error)
                results.append(item)
        return results

    async def cancel_batch(self, provider_batch_id: str) -> None:
        await self.client.batches.cancel(provider_batch_id)
