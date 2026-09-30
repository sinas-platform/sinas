"""Anthropic structured outputs (native output_config.format) + long-request
stream fallback.

History, because each step was a field bug:
- message_service builds a json_schema response_format from agent.output_schema;
  the Anthropic provider originally never read it, so every JSON contract with
  a Claude agent was silently prompt-and-parse.
- It was then implemented as forced tool use (`tool_choice: {type: "tool"}`).
  Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject forced tool use with a 400,
  so every output-schema agent without tools failed on the current models.
  Native `output_config.format` works on every current model (verified live,
  Haiku 4.5 through Opus 5.5) and is what this now uses.
- The Anthropic SDK refuses non-streaming requests whose estimated duration
  exceeds its limit, which surfaced as naked 500s. No threshold is encoded
  here: the SDK's own guard triggers an internal stream-accumulate that
  returns the identical Message object.
"""

import json
from types import SimpleNamespace

import pytest

from app.providers import AnthropicProvider
from app.providers.anthropic_provider import UnsupportedOutputSchema, close_object_schemas

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
    async def test_response_format_becomes_native_format(self):
        provider, captured = _provider(create_result=_text_response('{"steps": [1, 2]}'))
        result = await provider.complete(
            messages=[{"role": "user", "content": "plan"}],
            model="claude-opus-5-5",
            response_format=SCHEMA_RF,
        )

        params = captured["params"]
        fmt = params["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["schema"]["properties"] == {"steps": {"type": "array"}}
        assert fmt["schema"]["additionalProperties"] is False
        # The 400-ing mechanism is gone entirely
        assert "tool_choice" not in params
        assert "tools" not in params

        assert json.loads(result["content"]) == {"steps": [1, 2]}
        assert result["tool_calls"] is None

    async def test_nested_objects_are_closed_in_the_request(self):
        """The live-verified failure: a top-level-only fix still 400s."""
        rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"meta": {"type": "object", "properties": {"n": {"type": "number"}}}},
        }}}
        provider, captured = _provider(create_result=_text_response('{"meta": {"n": 1}}'))
        await provider.complete(
            messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5",
            response_format=rf,
        )
        meta = captured["params"]["output_config"]["format"]["schema"]["properties"]["meta"]
        assert meta["additionalProperties"] is False

    async def test_real_tools_win_over_response_format(self):
        """Tool-carrying requests keep prompt-based structured output."""
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
        assert "format" not in params.get("output_config", {})
        assert params["tools"][0]["name"] == "search"

    async def test_no_response_format_unchanged(self):
        provider, captured = _provider(create_result=_text_response("hi"))
        result = await provider.complete(
            messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5"
        )
        assert "tools" not in captured["params"]
        assert "output_config" not in captured["params"]
        assert result["content"] == "hi"

    async def test_a_response_format_without_a_schema_sends_no_format(self):
        """Nothing to enforce — a closed empty object would force `{}`."""
        provider, captured = _provider(create_result=_text_response("hi"))
        await provider.complete(
            messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5",
            response_format={"type": "json_schema", "json_schema": {"name": "x"}},
        )
        assert "output_config" not in captured["params"]

    async def test_effort_and_format_share_output_config(self):
        """Both live in output_config; neither may erase the other."""
        provider, captured = _provider(create_result=_text_response('{"steps": []}'))
        provider.effort = "low"
        await provider.complete(
            messages=[{"role": "user", "content": "x"}], model="claude-opus-5-5",
            response_format=SCHEMA_RF,
        )
        config = captured["params"]["output_config"]
        assert config["effort"] == "low"
        assert config["format"]["type"] == "json_schema"

    async def test_an_inexpressible_schema_fails_before_any_request(self):
        rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {
            "type": "object",
            "properties": {"tags": {"type": "object", "additionalProperties": {"type": "string"}}},
        }}}
        provider, captured = _provider(create_result=_text_response("{}"))
        with pytest.raises(UnsupportedOutputSchema, match=r"\$\.tags"):
            await provider.complete(
                messages=[{"role": "user", "content": "x"}], model="claude-sonnet-5",
                response_format=rf,
            )
        assert "params" not in captured, "a request was sent anyway"


class TestCloseObjectSchemas:
    def test_nested_properties_items_and_combinators_are_closed(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "object", "properties": {"x": {"type": "string"}}},
                "list": {"type": "array", "items": {"type": "object", "properties": {"y": {"type": "number"}}}},
                "either": {"anyOf": [
                    {"type": "object", "properties": {"p": {"type": "string"}}},
                    {"type": "string"},
                ]},
            },
            "$defs": {"Thing": {"type": "object", "properties": {"z": {"type": "boolean"}}}},
        }
        out = close_object_schemas(schema)
        assert out["additionalProperties"] is False
        assert out["properties"]["a"]["additionalProperties"] is False
        assert out["properties"]["list"]["items"]["additionalProperties"] is False
        assert out["properties"]["either"]["anyOf"][0]["additionalProperties"] is False
        assert "additionalProperties" not in out["properties"]["either"]["anyOf"][1]
        assert out["$defs"]["Thing"]["additionalProperties"] is False

    def test_required_is_left_alone(self):
        """Not needed by the API; adding it would make optional fields mandatory."""
        out = close_object_schemas({"type": "object", "properties": {"a": {"type": "string"}}})
        assert "required" not in out

    def test_explicit_false_is_kept_and_nullable_objects_are_closed(self):
        assert close_object_schemas({"type": "object", "additionalProperties": False}) == {
            "type": "object", "additionalProperties": False,
        }
        out = close_object_schemas({"type": ["object", "null"], "properties": {}})
        assert out["additionalProperties"] is False

    def test_non_object_schemas_are_untouched(self):
        for schema in ({"type": "string"}, {"type": "array", "items": {"type": "number"}}, True):
            assert close_object_schemas(schema) == schema

    @pytest.mark.parametrize("extra", [True, {}, {"type": "string"}])
    def test_free_form_objects_are_refused_with_their_path(self, extra):
        schema = {"type": "object", "properties": {
            "outer": {"type": "object", "properties": {"inner": {"type": "object", "additionalProperties": extra}}},
        }}
        with pytest.raises(UnsupportedOutputSchema) as exc:
            close_object_schemas(schema)
        assert "$.outer.inner" in str(exc.value)
        assert "additionalProperties" in str(exc.value)

    def test_the_callers_schema_is_never_mutated(self):
        """message_service passes a SHALLOW copy of agent.output_schema, so
        nested dicts are the agent's own — editing them would write through
        to the stored ORM object."""
        nested = {"type": "object", "properties": {"n": {"type": "number"}}}
        schema = {"type": "object", "properties": {"meta": nested}}
        close_object_schemas(schema)
        assert "additionalProperties" not in schema
        assert "additionalProperties" not in nested

    def test_the_error_cannot_be_mistaken_for_the_sdk_streaming_guard(self):
        """complete() re-routes ValueErrors that mention streaming."""
        with pytest.raises(UnsupportedOutputSchema) as exc:
            close_object_schemas({"type": "object", "additionalProperties": True})
        assert "streaming" not in str(exc.value).lower()
        assert isinstance(exc.value, ValueError)


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
        """The stream fallback must carry the same output_config.format."""
        provider, captured = _provider(
            create_raises=ValueError("...requires streaming..."),
            stream_final=_text_response('{"steps": ["a"]}'),
        )
        result = await provider.complete(
            messages=[{"role": "user", "content": "x"}],
            model="claude-sonnet-5",
            max_tokens=32000,
            response_format=SCHEMA_RF,
        )
        assert captured["streamed"] is True
        assert captured["stream_params"]["output_config"]["format"]["type"] == "json_schema"
        assert json.loads(result["content"]) == {"steps": ["a"]}
