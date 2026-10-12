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


async def _adapter_deltas(include_thinking: bool) -> list[dict]:
    from app.adapters.openai.endpoints import _passthrough_stream

    class Provider:
        async def stream(self, **_):
            yield {"thinking": "", "content": None}
            yield {"thinking": "Hmm", "content": None}
            yield {"content": "Hi", "finish_reason": "stop"}

    response = await _passthrough_stream(Provider(), [], "m", {}, include_thinking)
    frames = [
        json.loads(line[len("data: "):])
        async for part in response.body_iterator
        for line in (part if isinstance(part, str) else part.decode()).splitlines()
        if line.startswith("data: {")
    ]
    return [f["choices"][0]["delta"] for f in frames if f["choices"]]


async def test_openai_adapter_sends_the_text_as_reasoning_content_when_asked():
    deltas = await _adapter_deltas(include_thinking=True)
    assert [d["reasoning_content"] for d in deltas if d.get("reasoning_content") is not None] == ["", "Hmm"]
    assert any(d.get("content") == "Hi" for d in deltas)


async def test_openai_adapter_only_says_it_is_thinking_by_default():
    deltas = await _adapter_deltas(include_thinking=False)
    assert [d["reasoning_content"] for d in deltas if d.get("reasoning_content") is not None] == [""]
    assert any(d.get("content") == "Hi" for d in deltas)


class TestThinkingFilter:
    def _run(self, include: bool, chunks: list[dict]) -> list[dict]:
        from app.services.stream_thinking import ThinkingFilter

        show = ThinkingFilter(include)
        return [out for c in chunks if (out := show(c)) is not None]

    CHUNKS = [
        {"content": None, "tool_calls": None, "finish_reason": None, "thinking": "a"},
        {"content": None, "tool_calls": None, "finish_reason": None, "thinking": "b"},
        {"content": "Hi", "tool_calls": None, "finish_reason": None},
        {"type": "tool_start", "tool_call_id": "t"},
        {"content": None, "tool_calls": None, "finish_reason": None, "thinking": "c"},
        {"content": "Done", "tool_calls": None, "finish_reason": "stop"},
    ]

    def test_without_asking_only_the_start_of_each_thinking_round(self):
        out = self._run(False, self.CHUNKS)
        assert [c.get("thinking") for c in out] == ["", None, None, "", None]
        assert all(c.get("thinking") in (None, "") for c in out)

    def test_asked_everything_goes_through(self):
        assert self._run(True, self.CHUNKS) == self.CHUNKS

    def test_a_chunk_that_carries_more_keeps_the_rest(self):
        out = self._run(False, [
            {"content": None, "thinking": "a"},
            {"content": None, "thinking": "b", "usage": {"total_tokens": 3}},
        ])
        assert out == [{"content": None, "thinking": ""}, {"content": None, "usage": {"total_tokens": 3}}]


async def test_chat_stream_hides_the_text_unless_asked(client, admin_user, monkeypatch):
    from app.services.message_service import MessageService
    from tests.conftest import auth_headers

    async def fake_stream(self, **_):
        yield {"content": None, "tool_calls": None, "finish_reason": None, "thinking": "secret plan"}
        yield {"content": "Hi", "tool_calls": None, "finish_reason": "stop"}

    monkeypatch.setattr(MessageService, "send_message_stream", fake_stream)
    h = auth_headers(admin_user)
    r = await client.post("/api/v1/agents", json={"namespace": "think", "name": "bot"}, headers=h)
    assert r.status_code in (200, 201), r.text
    r = await client.post("/agents/think/bot/chats", json={}, headers=h)
    assert r.status_code in (200, 201), r.text
    chat = r.json()["id"]

    async def events(body: dict) -> list[dict]:
        r = await client.post(f"/chats/{chat}/messages/stream", json=body, headers=h)
        assert r.status_code == 200, r.text
        return [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:") and "{" in line]

    hidden = await events({"content": "hi"})
    assert "secret plan" not in json.dumps(hidden)
    assert any(e.get("thinking") == "" for e in hidden)
    shown = await events({"content": "hi", "include_thinking": True})
    assert any(e.get("thinking") == "secret plan" for e in shown)
