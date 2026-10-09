"""The trace context and baggage a request arrives with reach the model API.

Pinned here: a request's `traceparent` and `baggage` become the current
context while it is served, with members split over several `baggage` lines
kept; every provider type configured with `propagate_context` sends both on
with every call it makes for a request, batch submissions included, and one
that is not sends nothing; a batch item's body never carries them; a queued
job, a resumed one included, makes the context it was enqueued with current,
baggage included.

No network calls: requests go through an in-memory ASGI app, and providers
talk to an in-memory transport that records what they send.
"""
import asyncio
import json
from collections.abc import Callable

import anthropic
import httpx
import httpx2
import openai
import pytest
from opentelemetry import baggage as otel_baggage
from opentelemetry import trace
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.telemetry import attached, extract_trace_context, inject_trace_context
from app.middleware.trace_context import TraceContextMiddleware
from app.providers import (
    AnthropicProvider,
    AzureOpenAIProvider,
    MistralProvider,
    OllamaProvider,
    OpenAIProvider,
    ollama_provider,
)
from app.queue import agent_jobs

TRACE_ID = "4c79f60c11214eb38604f4ae0781bfb2"
TRACEPARENT = f"00-{TRACE_ID}-b2a7c1d9e8f30411-01"
BAGGAGE = "conversation_id=c-1,org_id=org-9"
MESSAGES = [{"role": "user", "content": "hi"}]

# Kept before any test swaps `httpx.AsyncClient` for the test transport.
_HttpxClient = httpx.AsyncClient
# How a call ends on the test transport's 400, once it has been sent.
_REFUSED = (openai.BadRequestError, anthropic.BadRequestError, httpx.HTTPStatusError)


def _seen_by_endpoint(headers) -> dict:
    async def endpoint(request):
        span = trace.get_current_span().get_span_context()
        return JSONResponse(
            {
                "trace_id": format(span.trace_id, "032x") if span.is_valid else None,
                "conversation_id": otel_baggage.get_baggage("conversation_id"),
                "org_id": otel_baggage.get_baggage("org_id"),
                "outbound": inject_trace_context(),
            }
        )

    app = TraceContextMiddleware(Starlette(routes=[Route("/", endpoint)]))
    return TestClient(app).get("/", headers=headers).json()


def test_a_request_is_served_inside_the_context_it_arrived_with():
    seen = _seen_by_endpoint({"traceparent": TRACEPARENT, "baggage": BAGGAGE})
    assert seen["trace_id"] == TRACE_ID
    assert seen["org_id"] == "org-9"
    # And it is what a call made while serving it would send on.
    assert seen["outbound"]["traceparent"].split("-")[1] == TRACE_ID
    assert "org_id=org-9" in seen["outbound"]["baggage"]


def test_baggage_split_over_several_lines_is_kept_whole():
    seen = _seen_by_endpoint(
        [
            ("traceparent", TRACEPARENT),
            ("baggage", "conversation_id=c-1"),
            ("baggage", "org_id=org-9"),
        ]
    )
    assert seen["conversation_id"] == "c-1"
    assert seen["org_id"] == "org-9"


def test_a_repeated_traceparent_is_no_trace_context():
    other = f"00-{'a' * 32}-{'b' * 16}-01"
    seen = _seen_by_endpoint([("traceparent", TRACEPARENT), ("traceparent", other)])
    assert seen["trace_id"] is None


def test_a_request_without_one_is_served_without_one():
    seen = _seen_by_endpoint({})
    assert seen["trace_id"] is None
    assert seen["org_id"] is None
    assert seen["outbound"] == {}


# ── providers ───────────────────────────────────────────────────────────────


class _Wire:
    """An in-memory transport that records each request and answers with a
    canned response per path, or a 400 that ends the call. The Anthropic SDK
    sends through `httpx2`, the others through `httpx`."""

    def __init__(self, answers: dict[str, dict] | None = None) -> None:
        self.sent: list = []
        self.answers = answers or {}

    def _answer(self, request, response_class):
        self.sent.append(request)
        for suffix, body in self.answers.items():
            if request.url.path.endswith(suffix):
                return response_class(200, json=body)
        return response_class(400, json={"error": {"message": "refused by the test"}})

    def client(self, **kwargs) -> httpx.AsyncClient:
        handle = lambda request: self._answer(request, httpx.Response)  # noqa: E731
        return _HttpxClient(transport=httpx.MockTransport(handle), **kwargs)

    def client2(self) -> httpx2.AsyncClient:
        handle = lambda request: self._answer(request, httpx2.Response)  # noqa: E731
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handle))


def _openai(wire: _Wire) -> OpenAIProvider:
    provider = OpenAIProvider(api_key="k", base_url="http://gateway.test/v1")
    provider.client = openai.AsyncOpenAI(
        api_key="k", base_url="http://gateway.test/v1", http_client=wire.client(), max_retries=0
    )
    return provider


def _azure(wire: _Wire) -> AzureOpenAIProvider:
    provider = AzureOpenAIProvider(api_key="k", azure_endpoint="http://gateway.test")
    provider.client = openai.AsyncAzureOpenAI(
        api_key="k",
        api_version=provider.api_version,
        azure_endpoint="http://gateway.test",
        http_client=wire.client(),
        max_retries=0,
    )
    return provider


def _mistral(wire: _Wire) -> MistralProvider:
    provider = MistralProvider(api_key="k", base_url="http://gateway.test/v1")
    provider.client = openai.AsyncOpenAI(
        api_key="k", base_url="http://gateway.test/v1", http_client=wire.client(), max_retries=0
    )
    return provider


def _anthropic(wire: _Wire) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="k", base_url="http://gateway.test")
    provider.client = anthropic.AsyncAnthropic(
        api_key="k", base_url="http://gateway.test", http_client=wire.client2(), max_retries=0
    )
    return provider


def _ollama(wire: _Wire, monkeypatch) -> OllamaProvider:
    monkeypatch.setattr(ollama_provider.httpx, "AsyncClient", wire.client)
    return OllamaProvider(base_url="http://gateway.test")


PROVIDERS: dict[str, Callable] = {
    "openai": lambda wire, mp: _openai(wire),
    "azure": lambda wire, mp: _azure(wire),
    "mistral": lambda wire, mp: _mistral(wire),
    "anthropic": lambda wire, mp: _anthropic(wire),
    "ollama": _ollama,
}


async def _complete(provider) -> None:
    await provider.complete(MESSAGES, model="m", max_tokens=1)


async def _stream(provider) -> None:
    async for _ in provider.stream(MESSAGES, model="m", max_tokens=1):
        pass


def _call_inside_the_context(provider, call) -> None:
    """Make one call with the caller's context current; the call ends on the
    test transport's 400, after it has been sent."""
    ctx = extract_trace_context({"traceparent": TRACEPARENT, "baggage": BAGGAGE})

    async def run() -> None:
        with attached(ctx):
            try:
                await call(provider)
            except _REFUSED:
                pass

    asyncio.run(run())


def _carries_the_context(request: httpx.Request) -> bool:
    return (
        request.headers.get("traceparent", "").split("-")[1:2] == [TRACE_ID]
        and request.headers.get("baggage") == BAGGAGE
    )


@pytest.mark.parametrize("call", [_complete, _stream], ids=["complete", "stream"])
@pytest.mark.parametrize("kind", PROVIDERS)
def test_every_provider_sends_the_context_only_when_configured_to(kind, call, monkeypatch):
    # Off by default: a public model API has no use for whom a call is for.
    wire = _Wire()
    _call_inside_the_context(PROVIDERS[kind](wire, monkeypatch), call)
    assert wire.sent
    assert not any("baggage" in r.headers or "traceparent" in r.headers for r in wire.sent)

    wire = _Wire()
    provider = PROVIDERS[kind](wire, monkeypatch)
    provider.propagate_context = True
    _call_inside_the_context(provider, call)
    assert wire.sent
    assert all(_carries_the_context(r) for r in wire.sent)


def test_a_payload_never_carries_the_context():
    provider = OpenAIProvider(api_key="k")
    provider.propagate_context = True
    ctx = extract_trace_context({"traceparent": TRACEPARENT, "baggage": BAGGAGE})
    with attached(ctx):
        params = provider._prepare_params(
            model="m", messages=MESSAGES, temperature=0.0, max_tokens=None, tools=None, kwargs={}
        )
    assert "extra_headers" not in params


BATCH_REQUEST = {"custom_id": "r-1", "model": "m", "messages": MESSAGES, "max_tokens": 1}


def test_an_openai_batch_carries_the_context_on_its_calls_not_in_its_items():
    uploaded = {
        "id": "file-1",
        "object": "file",
        "bytes": 1,
        "created_at": 0,
        "filename": "batch.jsonl",
        "purpose": "batch",
        "status": "processed",
    }
    wire = _Wire({"/files": uploaded})
    provider = _openai(wire)
    provider.propagate_context = True

    async def submit(p) -> None:
        await p.submit_batch([BATCH_REQUEST])

    _call_inside_the_context(provider, submit)
    upload, create = wire.sent
    assert upload.url.path.endswith("/files") and create.url.path.endswith("/batches")
    assert _carries_the_context(upload) and _carries_the_context(create)
    # The item itself is the chat request, and nothing else.
    line = next(part for part in upload.content.split(b"\r\n") if part.startswith(b'{"custom_id"'))
    assert "extra_headers" not in json.loads(line)["body"]


def test_an_anthropic_batch_carries_the_context():
    wire = _Wire()
    provider = _anthropic(wire)
    provider.propagate_context = True

    async def submit(p) -> None:
        await p.submit_batch([BATCH_REQUEST])

    _call_inside_the_context(provider, submit)
    (create,) = wire.sent
    assert create.url.path.endswith("/messages/batches")
    assert _carries_the_context(create)


# ── queued jobs ─────────────────────────────────────────────────────────────


def test_a_job_makes_its_enqueued_context_current_baggage_included():
    ctx = extract_trace_context({"traceparent": TRACEPARENT, "baggage": BAGGAGE})

    async def job():
        tracer = trace.get_tracer("test")
        with attached(ctx), tracer.start_as_current_span("agent.job"):
            return otel_baggage.get_baggage("conversation_id"), inject_trace_context()

    conversation, outbound = asyncio.run(job())
    assert conversation == "c-1"
    assert outbound["traceparent"].split("-")[1] == TRACE_ID
    # Outside the job, nothing is left behind.
    assert otel_baggage.get_baggage("conversation_id") is None


class _StoppedError(Exception):
    pass


class _RecordingRedis:
    """Records the baggage current at the job's first Redis call, then stops
    the job there."""

    def __init__(self) -> None:
        self.conversation: str | None = None

    async def _stop(self, *args, **kwargs):
        self.conversation = otel_baggage.get_baggage("conversation_id")
        raise _StoppedError

    get = set = _stop


@pytest.mark.parametrize(
    "job, extra",
    [
        ("execute_agent_resume_job", {"pending_approval_id": "a-1", "approved": True}),
        ("execute_agent_delegate_resume_job", {"conversation_context": {}}),
    ],
)
def test_a_resumed_job_runs_inside_its_enqueued_context(job, extra):
    redis = _RecordingRedis()
    kwargs = {
        "job_id": "j-1",
        "chat_id": "c-1",
        "user_id": "u-1",
        "user_token": "t",
        "channel_id": "ch-1",
        "trace_context": {"traceparent": TRACEPARENT, "baggage": BAGGAGE},
        **extra,
    }

    async def run() -> None:
        with pytest.raises(_StoppedError):
            await getattr(agent_jobs, job)({"redis": redis}, **kwargs)
        # Nothing is left behind once the job ends.
        assert otel_baggage.get_baggage("conversation_id") is None

    asyncio.run(run())
    assert redis.conversation == "c-1"


def test_attaching_nothing_changes_nothing():
    with attached(None):
        assert inject_trace_context() == {}
