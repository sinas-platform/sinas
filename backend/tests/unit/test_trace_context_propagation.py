"""The trace context and baggage a request arrives with reach the model API.

Pinned here: a request's `traceparent` and `baggage` become the current
context while it is served; a provider configured with `propagate_context`
sends both on with its calls, and one that is not sends nothing; a queued
job makes the context it was enqueued with current, baggage included.

No network calls: requests go through an in-memory ASGI app, and the
providers' request-building hooks are exercised directly.
"""
import asyncio

from opentelemetry import baggage as otel_baggage
from opentelemetry import trace
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.telemetry import attached, extract_trace_context, inject_trace_context
from app.middleware.trace_context import TraceContextMiddleware
from app.providers import AnthropicProvider, OpenAIProvider

TRACE_ID = "4c79f60c11214eb38604f4ae0781bfb2"
TRACEPARENT = f"00-{TRACE_ID}-b2a7c1d9e8f30411-01"
BAGGAGE = "conversation_id=c-1,org_id=org-9"


def _seen_by_endpoint(headers: dict[str, str]) -> dict:
    async def endpoint(request):
        span = trace.get_current_span().get_span_context()
        return JSONResponse(
            {
                "trace_id": format(span.trace_id, "032x") if span.is_valid else None,
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


def test_a_request_without_one_is_served_without_one():
    seen = _seen_by_endpoint({})
    assert seen["trace_id"] is None
    assert seen["org_id"] is None
    assert seen["outbound"] == {}


def _anthropic_params(provider: AnthropicProvider) -> dict:
    params = {"model": "m", "messages": [], "max_tokens": 1}
    provider._apply_context_headers(params)
    return params


def _openai_params(provider: OpenAIProvider) -> dict:
    return provider._prepare_params(
        model="m", messages=[], temperature=0.0, max_tokens=None, tools=None, kwargs={}
    )


def test_a_provider_sends_the_context_only_when_configured_to():
    ctx = extract_trace_context({"traceparent": TRACEPARENT, "baggage": BAGGAGE})
    anthropic = AnthropicProvider(api_key="k")
    openai = OpenAIProvider(api_key="k")
    with attached(ctx):
        # Off by default: a public model API has no use for whom a call is for.
        assert "extra_headers" not in _anthropic_params(anthropic)
        assert "extra_headers" not in _openai_params(openai)

        anthropic.propagate_context = openai.propagate_context = True
        for headers in (
            _anthropic_params(anthropic)["extra_headers"],
            _openai_params(openai)["extra_headers"],
        ):
            assert headers["traceparent"].split("-")[1] == TRACE_ID
            assert headers["baggage"] == BAGGAGE


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


def test_attaching_nothing_changes_nothing():
    with attached(None):
        assert inject_trace_context() == {}
