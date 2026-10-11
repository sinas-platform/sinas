"""Streamed thinking, on every provider that sends it.

A reasoning model can think for seconds before its first word. Every
provider dropped that thinking, so a client saw nothing at all until the
answer started. Now each stream chunk carries it as `thinking`, whatever the
provider calls it.
"""
import json
from types import SimpleNamespace

import httpx
from openai.types.chat import ChatCompletionChunk

from app.providers import AnthropicProvider, OpenAIProvider
from app.providers.mistral_provider import MistralProvider
from app.providers.ollama_provider import OllamaProvider


async def _collect(provider, **kwargs):
    return [c async for c in provider.stream(messages=[{"role": "user", "content": "hi"}], model="m", **kwargs)]


def _thinking(chunks):
    return [c["thinking"] for c in chunks if "thinking" in c]


def _content(chunks):
    return "".join(c["content"] for c in chunks if c.get("content"))


class _FakeStream:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._items:
            yield item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _openai_chunk(delta: dict, finish=None) -> ChatCompletionChunk:
    # Built like the SDK builds a streamed chunk: no validation, extra
    # fields kept.
    return ChatCompletionChunk.construct(**{
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    })


def _openai_client(chunks):
    async def create(**_):
        return _FakeStream(chunks)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def test_anthropic_says_it_is_thinking_then_streams_it():
    events = [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=None)),
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="thinking")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="thinking_delta", thinking="Let me see")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="signature_delta", signature="x")),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(type="content_block_start", index=1, content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="content_block_delta", index=1, delta=SimpleNamespace(type="text_delta", text="Hello")),
        SimpleNamespace(type="message_stop"),
    ]
    provider = AnthropicProvider(api_key="k")
    provider.client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **_: _FakeStream(events)))
    chunks = await _collect(provider)
    # The block start arrives first, empty: the model has started thinking.
    assert _thinking(chunks) == ["", "Let me see"]
    assert _content(chunks) == "Hello"


async def test_openai_compatible_reasoning_fields():
    provider = OpenAIProvider(api_key="k")
    provider.client = _openai_client([
        _openai_chunk({"role": "assistant", "reasoning_content": "Hmm"}),   # DeepSeek, vLLM, Gemini
        _openai_chunk({"reasoning": ", right"}),                            # OpenRouter, Groq
        _openai_chunk({"content": "Hi"}, finish="stop"),
    ])
    chunks = await _collect(provider)
    assert _thinking(chunks) == ["Hmm", ", right"]
    assert _content(chunks) == "Hi"


async def test_openai_without_reasoning_adds_nothing():
    provider = OpenAIProvider(api_key="k")
    provider.client = _openai_client([_openai_chunk({"content": "Hi"}, finish="stop")])
    assert _thinking(await _collect(provider)) == []


async def test_mistral_thinking_chunks():
    provider = MistralProvider(api_key="k")
    provider.client = _openai_client([
        _openai_chunk({"content": [{"type": "thinking", "thinking": [{"type": "text", "text": "Think"}]}]}),
        _openai_chunk({"content": [{"type": "text", "text": "Answer"}]}),
        _openai_chunk({"content": " here"}, finish="stop"),
    ])
    chunks = await _collect(provider)
    assert _thinking(chunks) == ["Think"]
    assert _content(chunks) == "Answer here"


async def test_ollama_thinking_field(monkeypatch):
    lines = [
        {"message": {"role": "assistant", "content": "", "thinking": "Pondering"}, "done": False},
        {"message": {"role": "assistant", "content": "Yes"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 1, "eval_count": 2},
    ]
    body = "\n".join(json.dumps(line) for line in lines).encode()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.providers.ollama_provider.httpx.AsyncClient", lambda **kw: real(transport=transport, **kw)
    )
    chunks = await _collect(OllamaProvider(base_url="http://ollama"))
    assert _thinking(chunks) == ["Pondering"]
    assert _content(chunks) == "Yes"


async def test_openai_adapter_passes_it_on_as_reasoning_content():
    from app.adapters.openai.endpoints import _passthrough_stream

    class Provider:
        async def stream(self, **_):
            yield {"thinking": "Hmm", "content": None}
            yield {"content": "Hi", "finish_reason": "stop"}

    response = await _passthrough_stream(Provider(), [], "m", {})
    frames = [
        json.loads(line[len("data: "):])
        async for part in response.body_iterator
        for line in (part if isinstance(part, str) else part.decode()).splitlines()
        if line.startswith("data: {")
    ]
    deltas = [f["choices"][0]["delta"] for f in frames if f["choices"]]
    assert {"reasoning_content": "Hmm"}.items() <= next(d for d in deltas if d.get("reasoning_content")).items()
    assert any(d.get("content") == "Hi" for d in deltas)
