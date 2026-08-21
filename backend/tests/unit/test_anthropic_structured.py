"""Anthropic structured outputs (forced tool use) + long-request stream fallback.

Two field bugs from the Gate-2 load weekend:
- message_service builds a json_schema response_format from agent.output_schema;
  OpenAI-family providers forward it, but the Anthropic provider built its
  params dict field-by-field and never read kwargs — every JSON contract with
  a Claude agent was silently prompt-and-parse.
- The Anthropic SDK refuses non-streaming requests whose estimated duration
  exceeds its limit (a function of max_tokens and model speed), which surfaced
  as naked 500s. No threshold is encoded here: the SDK's own guard triggers an
  internal stream-accumulate that returns the identical Message object.
"""

import json
from types import SimpleNamespace

import pytest

from app.providers import AnthropicProvider

SCHEMA_RF = {
    "type": "json_schema",
    "json_schema": {
        "name": "planner_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"steps": {"type": "array"}},
            "additionalProperties": False,
        },
    },
}


def _usage():
    return SimpleNamespace(
        input_tokens=10, output_tokens=5,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )


def _tool_use_response(name, payload):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="tu_1", name=name, input=payload)],
        usage=_usage(),
        stop_reason="tool_use",
    )


def _text_response(text):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=_usage(),
        stop_reason="end_turn",
    )


def _provider(create_result=None, create_raises=None, stream_final=None):
    provider = AnthropicProvider(api_key="k", enable_prompt_caching=False)
    captured = {}

    async def fake_create(**params):
        captured["params"] = params
        if create_raises:
            raise create_raises
        return create_result

    class _StreamCM:
        async def __aenter__(self):
            captured["streamed"] = True
            return self

        async def __aexit__(self, *a):
            return False

        async def get_final_message(self):
            return stream_final

    def fake_stream(**params):
        captured["stream_params"] = params
        return _StreamCM()

    provider.client = SimpleNamespace(
        messages=SimpleNamespace(create=fake_create, stream=fake_stream)
    )
    return provider, captured


class TestStructuredOutputs:
    async def test_response_format_becomes_forced_tool(self):
        provider, captured = _provider(
            create_result=_tool_use_response("planner_response", {"steps": [1, 2]})
        )
        result = await provider.complete(
            messages=[{"role": "user", "content": "plan"}],
            model="claude-sonnet-5",
            response_format=SCHEMA_RF,
        )

        params = captured["params"]
        (tool,) = params["tools"]
        assert tool["name"] == "planner_response"
        assert tool["input_schema"]["properties"] == {"steps": {"type": "array"}}
        assert params["tool_choice"] == {"type": "tool", "name": "planner_response"}

        # The tool call IS the answer: clean JSON content, no tool loop
        assert json.loads(result["content"]) == {"steps": [1, 2]}
        assert result["tool_calls"] is None
        assert result["finish_reason"] == "stop"

    async def test_real_tools_win_over_response_format(self):
        """Forcing tool_choice would kill the agent's actual tool loop, so
        tool-carrying requests keep prompt-based structured output."""
        provider, captured = _provider(create_result=_text_response("ok"))
        real_tools = [{
            "type": "function",
            "function": {"name": "search", "parameters": {"type": "object"}},
        }]
        await provider.complete(
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-5",
            tools=real_tools,
            response_format=SCHEMA_RF,
        )
        params = captured["params"]
        assert "tool_choice" not in params
        assert params["tools"][0]["name"] == "search"

    async def test_no_response_format_unchanged(self):
        provider, captured = _provider(create_result=_text_response("hi"))
        result = await provider.complete(
            messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5"
        )
        assert "tools" not in captured["params"]
        assert result["content"] == "hi"

    async def test_model_refusing_forced_tool_falls_back_to_text(self):
        provider, _ = _provider(create_result=_text_response('{"steps": []}'))
        result = await provider.complete(
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-5",
            response_format=SCHEMA_RF,
        )
        assert result["content"] == '{"steps": []}'


class TestLongRequestStreamFallback:
    async def test_sdk_streaming_guard_triggers_internal_stream(self):
        provider, captured = _provider(
            create_raises=ValueError(
                "Streaming is strongly recommended for operations that may "
                "take longer than 10 minutes."
            ),
            stream_final=_text_response("long answer"),
        )
        result = await provider.complete(
            messages=[{"role": "user", "content": "write a book"}],
            model="claude-sonnet-5",
            max_tokens=32000,
        )
        assert captured["streamed"] is True
        assert captured["stream_params"]["max_tokens"] == 32000
        assert result["content"] == "long answer"
        assert result["finish_reason"] == "end_turn"

    async def test_unrelated_valueerror_propagates(self):
        provider, _ = _provider(create_raises=ValueError("bad temperature"))
        with pytest.raises(ValueError, match="bad temperature"):
            await provider.complete(
                messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5"
            )

    async def test_guard_plus_structured_output_compose(self):
        provider, captured = _provider(
            create_raises=ValueError("...requires streaming..."),
            stream_final=_tool_use_response("planner_response", {"steps": ["a"]}),
        )
        result = await provider.complete(
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-5",
            max_tokens=32000,
            response_format=SCHEMA_RF,
        )
        assert captured["streamed"] is True
        assert json.loads(result["content"]) == {"steps": ["a"]}
        assert result["finish_reason"] == "stop"
