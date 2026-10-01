"""Adopt the W3C trace context and baggage a request arrives with.

A caller that is itself traced sends `traceparent` (and `tracestate`), and may
name in `baggage` whom the request is for. This middleware makes them the
current OpenTelemetry context for the request, so spans recorded while serving
it join the caller's trace, and a provider configured to propagate context
(`propagate_context`) passes both on to the model API it calls.

It works whether or not tracing is enabled: the OpenTelemetry API carries the
context with no SDK installed. A pure ASGI middleware rather than
`BaseHTTPMiddleware`, so the context is current in the endpoint itself.
"""

from opentelemetry import context as otel_context
from opentelemetry.propagate import extract
from starlette.types import ASGIApp, Receive, Scope, Send

_PROPAGATED = (b"traceparent", b"tracestate", b"baggage")


class TraceContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        carrier = {
            name.decode("latin-1"): value.decode("latin-1")
            for name, value in scope.get("headers") or []
            if name in _PROPAGATED
        }
        if not carrier:
            await self.app(scope, receive, send)
            return
        token = otel_context.attach(extract(carrier))
        try:
            await self.app(scope, receive, send)
        finally:
            otel_context.detach(token)
