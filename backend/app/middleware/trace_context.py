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

from collections.abc import Iterable

from opentelemetry import context as otel_context
from opentelemetry.propagate import extract
from starlette.types import ASGIApp, Receive, Scope, Send

_PROPAGATED = (b"traceparent", b"tracestate", b"baggage")


def _carrier(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """The propagated headers of a request, one entry per name.

    W3C `baggage` and `tracestate` may each arrive split over several header
    lines; their members are combined, in order, as one comma-separated list
    (RFC 9110 field-line combination), so none is lost. Repeated
    `traceparent` lines combine the same way into a value no propagator
    accepts, so the request is served with no trace context, as the W3C
    trace-context test suite expects of a duplicated `traceparent`.
    """
    values: dict[str, list[str]] = {}
    for name, value in headers:
        if name in _PROPAGATED:
            values.setdefault(name.decode("latin-1"), []).append(value.decode("latin-1"))
    return {name: ",".join(parts) for name, parts in values.items()}


class TraceContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        carrier = _carrier(scope.get("headers") or [])
        if not carrier:
            await self.app(scope, receive, send)
            return
        token = otel_context.attach(extract(carrier))
        try:
            await self.app(scope, receive, send)
        finally:
            otel_context.detach(token)
