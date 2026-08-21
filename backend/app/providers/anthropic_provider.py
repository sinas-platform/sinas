"""Anthropic LLM provider implementation."""
from collections.abc import AsyncIterator
from typing import Any, Optional

from anthropic import AsyncAnthropic

from .base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Anthropic (Claude) API provider."""

    supports_batch = True

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_prompt_caching: bool = True,
    ):
        super().__init__(api_key, base_url)
        self.client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        self.enable_prompt_caching = enable_prompt_caching

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
            "temperature": temperature if temperature is not None else 1.0,  # Anthropic requires valid number
            "max_tokens": max_tokens or 16384,  # Anthropic requires max_tokens
        }

        if system_message:
            params["system"] = system_message

        if tools:
            # Convert OpenAI tool format to Anthropic format
            params["tools"] = self._convert_tools_to_anthropic(tools)

        # Structured outputs: Anthropic has no response_format param; its
        # structured-output path is forced tool use. Only when the request
        # carries no real tools — forcing a tool_choice would otherwise
        # stop the agent's actual tool loop, and tool-using agents already
        # get the schema instruction in their system prompt.
        structured_tool = None
        response_format = kwargs.get("response_format")
        if response_format and not tools and response_format.get("type") == "json_schema":
            js = response_format.get("json_schema") or {}
            structured_tool = {
                "name": (js.get("name") or "structured_response")[:64],
                "description": (
                    "Deliver the final response in the required JSON shape. "
                    "Call exactly once with the complete answer."
                ),
                "input_schema": js.get("schema") or {"type": "object"},
            }
            params["tools"] = [structured_tool]
            params["tool_choice"] = {"type": "tool", "name": structured_tool["name"]}

        if self.enable_prompt_caching:
            self._apply_cache_control(params)

        try:
            response = await self.client.messages.create(**params)
        except ValueError as e:
            # The SDK refuses non-streaming requests whose ESTIMATED duration
            # exceeds its limit (derived from max_tokens and model speed).
            # Rather than encode any threshold ourselves, catch its guard and
            # accumulate a stream into the identical Message object.
            if "streaming" not in str(e).lower():
                raise
            async with self.client.messages.stream(**params) as s:
                response = await s.get_final_message()

        if structured_tool is not None:
            # The forced tool call IS the answer: return its input as the
            # message content so downstream parses one clean JSON document.
            for block in response.content:
                if block.type == "tool_use" and block.name == structured_tool["name"]:
                    return {
                        "content": self._serialize_args(block.input),
                        "tool_calls": None,
                        "usage": self.extract_usage(response),
                        # stop_reason is "tool_use" mechanically; semantically
                        # this is a completed final answer.
                        "finish_reason": "stop",
                    }
            # Model refused the tool (rare) — fall through to normal parsing.

        # Extract content (Anthropic returns list of content blocks)
        content = ""
        tool_calls = []

        for idx, block in enumerate(response.content):
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                # Generate fallback ID if not provided
                call_id = block.id if block.id else f"toolu_{idx}"
                tool_calls.append({
                    "id": call_id,
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
            "temperature": temperature if temperature is not None else 1.0,  # Anthropic requires valid number
            "max_tokens": max_tokens or 16384,
        }

        if system_message:
            params["system"] = system_message

        if tools:
            params["tools"] = self._convert_tools_to_anthropic(tools)

        if self.enable_prompt_caching:
            self._apply_cache_control(params)

        # Track tool calls being built across chunks
        current_tool_calls = {}
        current_content = ""

        # Token usage arrives in message_start (input + cache counts) and
        # message_delta (cumulative output); emitted on the final chunk.
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0

        async with self.client.messages.stream(**params) as stream:
            async for event in stream:
                chunk_data = {
                    "content": None,
                    "tool_calls": None,
                    "finish_reason": None,
                }

                if event.type == "message_start":
                    usage = getattr(event.message, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "input_tokens", 0) or 0
                        output_tokens = getattr(usage, "output_tokens", 0) or 0
                        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
                        cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

                elif event.type == "content_block_start":
                    if event.content_block.type == "text":
                        pass  # Text will come in content_block_delta
                    elif event.content_block.type == "tool_use":
                        # Start tracking a new tool call
                        idx = event.index
                        call_id = event.content_block.id if event.content_block.id else f"toolu_{idx}"
                        current_tool_calls[idx] = {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": event.content_block.name,
                                "arguments": "",
                            },
                            "index": idx,
                        }

                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        chunk_data["content"] = event.delta.text
                        current_content += event.delta.text
                    elif hasattr(event.delta, "partial_json"):
                        # partial_json is an incremental delta, not cumulative
                        idx = event.index
                        if idx in current_tool_calls:
                            delta = event.delta.partial_json
                            # Accumulate for content_block_stop fallback
                            current_tool_calls[idx]["function"]["arguments"] += delta

                            if delta:
                                chunk_data["tool_calls"] = [{
                                    "id": current_tool_calls[idx]["id"],
                                    "type": "function",
                                    "function": {
                                        "name": current_tool_calls[idx]["function"]["name"],
                                        "arguments": delta,
                                    },
                                    "index": idx,
                                }]

                elif event.type == "content_block_stop":
                    # Emit completed tool call ONLY for empty-input tools (no deltas sent).
                    # Tools with arguments were already streamed via partial_json deltas.
                    idx = event.index
                    if idx in current_tool_calls:
                        tc = current_tool_calls[idx]
                        if not tc["function"]["arguments"]:
                            chunk_data["tool_calls"] = [{**tc, "index": idx}]

                elif event.type == "message_delta":
                    if hasattr(event.delta, "stop_reason") and event.delta.stop_reason:
                        chunk_data["finish_reason"] = event.delta.stop_reason
                    usage = getattr(event, "usage", None)
                    if usage:
                        if getattr(usage, "output_tokens", None) is not None:
                            output_tokens = usage.output_tokens
                        if getattr(usage, "input_tokens", None) is not None:
                            input_tokens = usage.input_tokens

                elif event.type == "message_stop":
                    chunk_data["finish_reason"] = chunk_data["finish_reason"] or "stop"
                    # input_tokens excludes cache reads/writes; prompt_tokens
                    # includes them (see extract_usage).
                    prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
                    chunk_data["usage"] = {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": prompt_tokens + output_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "cache_write_tokens": cache_write_tokens,
                    }

                yield chunk_data

    def _apply_cache_control(self, params: dict[str, Any]) -> None:
        """Mark prompt-cache breakpoints on the request (in place).

        Anthropic caches the request prefix up to each ``cache_control``
        marker (prefix order: tools, then system, then messages). Three
        breakpoints (of max 4 allowed): the last tool and the system prompt
        cover the static per-agent part; the last message gives a rolling
        breakpoint so each call in a tool loop reuses the previous call's
        cache. Prefixes shorter than the model's cacheable minimum are
        silently not cached — the markers are harmless then.
        """
        cache_control = {"type": "ephemeral"}

        tools = params.get("tools")
        if tools:
            tools[-1]["cache_control"] = cache_control

        system = params.get("system")
        if isinstance(system, str) and system:
            params["system"] = [
                {"type": "text", "text": system, "cache_control": cache_control}
            ]

        messages = params.get("messages")
        if messages:
            last = messages[-1]
            content = last.get("content")
            if isinstance(content, str):
                if content:
                    last["content"] = [
                        {"type": "text", "text": content, "cache_control": cache_control}
                    ]
            elif isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict):
                    # Copy instead of mutating: the block list may be shared
                    # with the caller's message objects.
                    last["content"] = [
                        *content[:-1],
                        {**last_block, "cache_control": cache_control},
                    ]

    def _convert_messages_to_anthropic(
        self, messages: list[dict[str, Any]]
    ) -> tuple[Optional[str], list[dict[str, Any]]]:
        """Convert OpenAI-style messages to Anthropic format.

        Anthropic requires:
        - Strict user/assistant alternation
        - First message must be user
        - No consecutive same-role messages

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
                tool_call_id = msg.get("tool_call_id")
                # Skip tool results without valid tool_call_id (Anthropic requirement)
                if not tool_call_id or not isinstance(tool_call_id, str):
                    continue

                new_msg = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": msg.get("content", ""),
                        }
                    ],
                }
                converted_messages.append(new_msg)
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

                # Skip empty messages
                if not content:
                    continue

                # Handle multimodal content (list of content blocks)
                if isinstance(content, list):
                    new_msg = {"role": role, "content": content}
                else:
                    # Plain text content
                    new_msg = {"role": role, "content": content}

                converted_messages.append(new_msg)
                continue

        # Merge consecutive messages with same role (Anthropic requires alternation)
        merged_messages = []
        for msg in converted_messages:
            if merged_messages and merged_messages[-1]["role"] == msg["role"]:
                # Merge with previous message
                prev_content = merged_messages[-1]["content"]
                curr_content = msg["content"]

                # Convert both to list format for merging
                if isinstance(prev_content, str):
                    prev_content = [{"type": "text", "text": prev_content}]
                elif not isinstance(prev_content, list):
                    prev_content = [{"type": "text", "text": str(prev_content)}]

                if isinstance(curr_content, str):
                    curr_content = [{"type": "text", "text": curr_content}]
                elif not isinstance(curr_content, list):
                    curr_content = [{"type": "text", "text": str(curr_content)}]

                # Merge content blocks
                merged_messages[-1]["content"] = prev_content + curr_content
            else:
                merged_messages.append(msg)

        # Safety net: ensure every tool_use has a matching tool_result immediately after.
        # Anthropic requires tool_result in the user message right after the assistant
        # message containing tool_use. If missing, inject a synthetic error result.
        repaired = []
        for i, msg in enumerate(merged_messages):
            repaired.append(msg)
            if msg["role"] == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    tool_use_ids = [
                        block["id"] for block in content
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
                    ]
                    if tool_use_ids:
                        # Check if next message has matching tool_results
                        next_msg = merged_messages[i + 1] if i + 1 < len(merged_messages) else None
                        existing_result_ids = set()
                        if next_msg and next_msg.get("role") == "user":
                            next_content = next_msg.get("content", [])
                            if isinstance(next_content, list):
                                existing_result_ids = {
                                    block.get("tool_use_id") for block in next_content
                                    if isinstance(block, dict) and block.get("type") == "tool_result"
                                }

                        missing_ids = [tid for tid in tool_use_ids if tid not in existing_result_ids]
                        if missing_ids:
                            # Inject synthetic tool_result in a user message
                            synthetic_results = [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tid,
                                    "content": "[Error: tool call was interrupted]",
                                }
                                for tid in missing_ids
                            ]
                            if next_msg and next_msg.get("role") == "user":
                                # Prepend to existing user message content
                                next_content = next_msg.get("content", [])
                                if isinstance(next_content, str):
                                    next_content = [{"type": "text", "text": next_content}]
                                next_msg["content"] = synthetic_results + next_content
                            else:
                                # Insert a new user message with just the tool_results
                                repaired.append({"role": "user", "content": synthetic_results})

        merged_messages = repaired

        # Ensure first message is user (Anthropic requirement)
        if merged_messages and merged_messages[0]["role"] != "user":
            merged_messages.insert(0, {"role": "user", "content": "Continue the conversation."})

        # Ensure last message is user (required by models that don't support prefill)
        if merged_messages and merged_messages[-1]["role"] != "user":
            merged_messages.append({"role": "user", "content": "Continue."})

        return system_message, merged_messages

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
        """Extract token usage from Anthropic response.

        Anthropic's input_tokens EXCLUDES tokens read from or written to the
        prompt cache; prompt_tokens here includes them (matching OpenAI
        semantics), with the cache portions also reported separately.
        """
        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            prompt_tokens = (usage.input_tokens or 0) + cache_read + cache_write
            completion_tokens = usage.output_tokens or 0
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            }
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    # ── Message Batches API ───────────────────────────────────────────────

    def _build_batch_params(self, request: dict[str, Any]) -> dict[str, Any]:
        """Build per-request params for a batch item — same conversion and
        cache-control path as complete()/stream(), minus tools (batch mode
        is tool-less by contract)."""
        system_message, filtered_messages = self._convert_messages_to_anthropic(
            request["messages"]
        )
        temperature = request.get("temperature")
        params: dict[str, Any] = {
            "model": request["model"],
            "messages": filtered_messages,
            "temperature": temperature if temperature is not None else 1.0,
            "max_tokens": request.get("max_tokens") or 16384,
        }
        if system_message:
            params["system"] = system_message
        if self.enable_prompt_caching:
            # Batch requests sharing an agent's system prompt can still land
            # opportunistic cache hits — the discounts stack.
            self._apply_cache_control(params)
        return params

    async def submit_batch(self, requests: list[dict[str, Any]]) -> str:
        batch = await self.client.messages.batches.create(
            requests=[
                {"custom_id": req["custom_id"], "params": self._build_batch_params(req)}
                for req in requests
            ]
        )
        return batch.id

    async def get_batch_status(self, provider_batch_id: str) -> dict[str, Any]:
        batch = await self.client.messages.batches.retrieve(provider_batch_id)
        return {
            "status": batch.processing_status,
            "ended": batch.processing_status == "ended",
        }

    async def fetch_batch_results(self, provider_batch_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        # SDK-normalized statuses: succeeded | errored | canceled | expired
        status_map = {"canceled": "cancelled"}
        async for entry in await self.client.messages.batches.results(provider_batch_id):
            result_type = entry.result.type
            item: dict[str, Any] = {
                "custom_id": entry.custom_id,
                "status": status_map.get(result_type, result_type),
                "content": None,
                "usage": None,
                "error": None,
            }
            if result_type == "succeeded":
                message = entry.result.message
                item["content"] = "".join(
                    block.text for block in message.content if block.type == "text"
                )
                item["usage"] = self.extract_usage(message)
            elif result_type == "errored":
                item["error"] = str(entry.result.error)
            elif result_type == "expired":
                item["error"] = "provider_batch_expired"
            results.append(item)
        return results

    async def cancel_batch(self, provider_batch_id: str) -> None:
        await self.client.messages.batches.cancel(provider_batch_id)
