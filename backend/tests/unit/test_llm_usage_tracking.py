"""Unit tests for LLM token-usage tracking.

Covers the UsageTrackingProvider wrapper (recording on complete, stream,
errors and abandoned streams) and the usage emission added to the provider
stream() implementations. No network calls: provider clients are stubbed.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.providers import tracking
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.azure_openai_provider import AzureOpenAIProvider
from app.providers.base import BaseLLMProvider
from app.providers.mistral_provider import MistralProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.tracking import UsageTrackingProvider

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class FakeProvider(BaseLLMProvider):
    """Inner provider stub with scriptable chunks/usage/errors."""

    def __init__(self, chunks=None, usage=None, complete_error=None, fail_after=None):
        super().__init__(api_key="k")
        self._chunks = chunks or []
        self._usage = usage
        self._complete_error = complete_error
        self._fail_after = fail_after

    async def complete(self, messages, model, tools=None, temperature=0.7, max_tokens=None, **kwargs):
        if self._complete_error:
            raise self._complete_error
        return {"content": "hi", "tool_calls": None, "usage": self._usage, "finish_reason": "stop"}

    async def stream(self, messages, model, tools=None, temperature=0.7, max_tokens=None, **kwargs):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise RuntimeError("boom")
            yield chunk

    def format_tool_calls(self, tool_calls):
        return []

    def extract_usage(self, response):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


@pytest.fixture
def recorded(monkeypatch):
    """Capture llm_usage rows instead of writing to the database."""
    records = []

    async def fake_insert(values):
        records.append(values)

    monkeypatch.setattr(tracking, "_insert_usage", fake_insert)
    return records


async def _drain_tasks():
    # Let fire-and-forget recording tasks run
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_complete_records_usage_and_context(recorded):
    provider = UsageTrackingProvider(
        FakeProvider(usage=USAGE),
        provider_name="my-openai",
        provider_type="openai",
        context={"user_id": "u1", "chat_id": "c1", "agent": "ns/agent", "source": "chat"},
    )

    response = await provider.complete(messages=[], model="gpt-x")
    await _drain_tasks()

    assert response["usage"] == USAGE
    assert len(recorded) == 1
    row = recorded[0]
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    assert row["streamed"] is False
    assert row["error"] is None
    assert row["model"] == "gpt-x"
    assert row["provider_name"] == "my-openai"
    assert row["provider_type"] == "openai"
    assert row["user_id"] == "u1"
    assert row["chat_id"] == "c1"
    assert row["agent"] == "ns/agent"
    assert row["source"] == "chat"
    assert row["latency_ms"] >= 0


async def test_complete_error_recorded_and_reraised(recorded):
    provider = UsageTrackingProvider(
        FakeProvider(complete_error=RuntimeError("api down")), provider_name="p", provider_type="openai"
    )

    with pytest.raises(RuntimeError, match="api down"):
        await provider.complete(messages=[], model="m")
    await _drain_tasks()

    assert len(recorded) == 1
    assert recorded[0]["error"] == "api down"
    assert recorded[0]["total_tokens"] == 0


async def test_stream_records_usage_from_final_chunk(recorded):
    chunks = [
        {"content": "hel", "tool_calls": None, "finish_reason": None},
        {"content": "lo", "tool_calls": None, "finish_reason": "stop"},
        {"content": None, "tool_calls": None, "finish_reason": None, "usage": USAGE},
    ]
    provider = UsageTrackingProvider(FakeProvider(chunks=chunks), provider_name="p", provider_type="openai")

    seen = [c async for c in provider.stream(messages=[], model="m")]
    await _drain_tasks()

    assert seen == chunks  # chunks pass through unchanged
    assert len(recorded) == 1
    row = recorded[0]
    assert row["streamed"] is True
    assert row["error"] is None
    assert row["total_tokens"] == 15


async def test_stream_error_recorded_with_partial_usage(recorded):
    chunks = [{"content": "a", "tool_calls": None, "finish_reason": None}]
    provider = UsageTrackingProvider(
        FakeProvider(chunks=chunks + chunks, fail_after=1), provider_name="p", provider_type="openai"
    )

    with pytest.raises(RuntimeError, match="boom"):
        async for _ in provider.stream(messages=[], model="m"):
            pass
    await _drain_tasks()

    assert len(recorded) == 1
    assert recorded[0]["error"] == "boom"
    assert recorded[0]["streamed"] is True
    assert recorded[0]["total_tokens"] == 0


async def test_abandoned_stream_recorded(recorded):
    chunks = [
        {"content": "a", "tool_calls": None, "finish_reason": None},
        {"content": "b", "tool_calls": None, "finish_reason": None},
    ]
    provider = UsageTrackingProvider(FakeProvider(chunks=chunks), provider_name="p", provider_type="openai")

    agen = provider.stream(messages=[], model="m")
    await agen.__anext__()
    await agen.aclose()  # consumer walks away mid-stream
    await _drain_tasks()

    assert len(recorded) == 1
    assert recorded[0]["error"] == "stream_abandoned"
    assert recorded[0]["streamed"] is True


# --- Provider stream() usage emission ---


def _openai_style_provider(provider, chunks, captured):
    async def fake_create(**params):
        captured.update(params)

        async def gen():
            for c in chunks:
                yield c

        return gen()

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    return provider


async def test_openai_stream_requests_and_emits_usage():
    content_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason=None)],
        usage=None,
    )
    usage_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
    )
    captured = {}
    provider = _openai_style_provider(OpenAIProvider(api_key="k"), [content_chunk, usage_chunk], captured)

    out = [c async for c in provider.stream(messages=[{"role": "user", "content": "x"}], model="m")]

    assert captured["stream_options"] == {"include_usage": True}
    assert out[0]["content"] == "hi"
    assert "usage" not in out[0]
    assert out[-1]["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


async def test_azure_drop_params_strips_stream_options():
    provider = AzureOpenAIProvider(
        api_key="k",
        azure_endpoint="https://example.openai.azure.com/",
        drop_params=["stream_options"],
    )
    captured = {}
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")],
        usage=None,
    )
    _openai_style_provider(provider, [chunk], captured)

    out = [c async for c in provider.stream(messages=[{"role": "user", "content": "x"}], model="dep")]

    assert "stream_options" not in captured
    assert out[0]["content"] == "hi"


async def test_mistral_stream_emits_usage_from_final_chunk():
    content_chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason=None)],
        usage=None,
    )
    usage_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
    )
    captured = {}
    provider = _openai_style_provider(MistralProvider(api_key="k"), [content_chunk, usage_chunk], captured)

    out = [c async for c in provider.stream(messages=[{"role": "user", "content": "x"}], model="m")]

    assert out[-1]["usage"] == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}


class _FakeAnthropicStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield e


async def test_anthropic_stream_emits_usage_on_message_stop():
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=12, output_tokens=1)),
        ),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(text="hi"), index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=9, input_tokens=None),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    provider = AnthropicProvider(api_key="k")
    provider.client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **params: _FakeAnthropicStream(events))
    )

    out = [c async for c in provider.stream(messages=[{"role": "user", "content": "x"}], model="m")]

    final = out[-1]
    assert final["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 9,
        "total_tokens": 21,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    # stop_reason from message_delta still surfaces
    assert any(c["finish_reason"] == "end_turn" for c in out)
