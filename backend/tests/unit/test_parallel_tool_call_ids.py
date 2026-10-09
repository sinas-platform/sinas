"""Parallel tool calls in one step must keep distinct ids (issue #195).

OpenAI-style streaming sends each tool call as a first delta carrying the
real id, then argument fragments with id=None. The OpenAI provider used to
invent `call_<position-in-delta>` for every id-less fragment — always
`call_0`, one fragment per chunk — and the accumulator wrote it over the real
id. Two parallel calls both became `call_0`; OpenAI then rejected the second
tool result ("messages with role 'tool' must be a response to a preceding
message with 'tool_calls'", messages.[4]) and, since the ids were persisted,
every later turn of that chat too. Anthropic/Mistral were unaffected.
"""

from types import SimpleNamespace

from openai.types.chat.chat_completion_chunk import (
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)

from app.providers.openai_provider import OpenAIProvider
from app.services.tool_execution import accumulate_tool_call_delta, validate_tool_calls


def _delta(index: int, id: str | None, name: str | None = None, args: str | None = None):
    return ChoiceDeltaToolCall(
        index=index,
        id=id,
        type="function" if id else None,
        function=ChoiceDeltaToolCallFunction(name=name, arguments=args),
    )


# The exact chunk sequence OpenAI emits for two parallel calls.
TWO_PARALLEL_CALLS = [
    [_delta(0, "call_ABC", "lookup_weather", "")],
    [_delta(0, None, None, '{"city":')],
    [_delta(0, None, None, '"Paris"}')],
    [_delta(1, "call_XYZ", "lookup_time", "")],
    [_delta(1, None, None, '{"city":"Paris"}')],
]


class TestProviderFormatting:
    def test_streaming_fragment_without_id_gets_no_fallback(self):
        provider = OpenAIProvider(api_key="test-key")
        [tc] = provider.format_tool_calls([_delta(1, None, None, '{"x')])
        assert tc["id"] is None
        assert tc["index"] == 1

    def test_streaming_first_delta_keeps_real_id(self):
        provider = OpenAIProvider(api_key="test-key")
        [tc] = provider.format_tool_calls([_delta(1, "call_XYZ", "lookup_time", "")])
        assert tc["id"] == "call_XYZ"

    def test_non_streaming_shape_without_id_still_gets_positional_fallback(self):
        """No `index` attribute = complete (non-streamed) call list; ids are
        synthesised per position so they stay distinct."""
        provider = OpenAIProvider(api_key="test-key")
        calls = [
            SimpleNamespace(id=None, type="function", function=SimpleNamespace(name="a", arguments="{}")),
            SimpleNamespace(id=None, type="function", function=SimpleNamespace(name="b", arguments="{}")),
        ]
        ids = [tc["id"] for tc in provider.format_tool_calls(calls)]
        assert ids == ["call_0", "call_1"]


class TestAccumulator:
    def test_two_parallel_calls_keep_their_own_ids(self):
        provider = OpenAIProvider(api_key="test-key")
        acc: list[dict] = []
        for chunk in TWO_PARALLEL_CALLS:
            for tc in provider.format_tool_calls(chunk):
                accumulate_tool_call_delta(acc, tc)

        assert [tc["id"] for tc in acc] == ["call_ABC", "call_XYZ"]
        assert acc[0]["function"] == {"name": "lookup_weather", "arguments": '{"city":"Paris"}'}
        assert acc[1]["function"] == {"name": "lookup_time", "arguments": '{"city":"Paris"}'}

    def test_a_later_delta_never_overwrites_an_established_id(self):
        """Defence in depth: even a provider that still stamps a fallback id on
        fragments (the pre-fix behaviour, or a third-party gateway) cannot
        collapse the calls."""
        acc: list[dict] = []
        accumulate_tool_call_delta(acc, {"id": "call_ABC", "index": 0, "function": {"name": "a", "arguments": ""}})
        accumulate_tool_call_delta(acc, {"id": "call_0", "index": 0, "function": {"arguments": "{}"}})
        accumulate_tool_call_delta(acc, {"id": "call_XYZ", "index": 1, "function": {"name": "b", "arguments": ""}})
        accumulate_tool_call_delta(acc, {"id": "call_0", "index": 1, "function": {"arguments": "{}"}})
        assert [tc["id"] for tc in acc] == ["call_ABC", "call_XYZ"]

    def test_extra_fields_still_round_trip(self):
        """Gemini's thought_signature must survive the refactor."""
        acc: list[dict] = []
        accumulate_tool_call_delta(
            acc,
            {"id": "c1", "index": 0, "thought_signature": "sig", "function": {"name": "a", "arguments": "{}", "extra": 1}},
        )
        assert acc[0]["thought_signature"] == "sig"
        assert acc[0]["function"]["extra"] == 1


class TestValidateToolCalls:
    def test_missing_ids_are_filled_per_position(self):
        """A gateway that never sends ids still yields distinct, usable calls."""
        out = validate_tool_calls(
            [
                {"id": None, "function": {"name": "a", "arguments": "{}"}},
                {"id": None, "function": {"name": "b", "arguments": "{}"}},
            ]
        )
        assert [tc["id"] for tc in out] == ["call_0", "call_1"]

    def test_duplicate_ids_are_made_unique_not_dropped(self):
        out = validate_tool_calls(
            [
                {"id": "call_0", "function": {"name": "a", "arguments": "{}"}},
                {"id": "call_0", "function": {"name": "b", "arguments": "{}"}},
            ]
        )
        assert len(out) == 2
        assert out[0]["id"] == "call_0"
        assert out[1]["id"] == "call_0_1"

    def test_renamed_id_cannot_collide_with_a_later_real_id(self):
        out = validate_tool_calls(
            [
                {"id": "x", "function": {"name": "a", "arguments": "{}"}},
                {"id": "x_2", "function": {"name": "b", "arguments": "{}"}},
                {"id": "x", "function": {"name": "c", "arguments": "{}"}},
            ]
        )
        ids = [tc["id"] for tc in out]
        assert len(ids) == 3 and len(set(ids)) == 3, ids

    def test_distinct_ids_untouched(self):
        calls = [
            {"id": "call_ABC", "function": {"name": "a", "arguments": "{}"}},
            {"id": "call_XYZ", "function": {"name": "b", "arguments": "{}"}},
        ]
        assert [tc["id"] for tc in validate_tool_calls(calls)] == ["call_ABC", "call_XYZ"]

    def test_nameless_call_still_skipped(self):
        assert validate_tool_calls([{"id": "x", "function": {"name": "", "arguments": "{}"}}]) == []
