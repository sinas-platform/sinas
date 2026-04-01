"""OpenTelemetry instrumentation for SINAS.

Opt-in via OTEL_ENABLED=true + OTEL_EXPORTER_ENDPOINT. When disabled,
all spans are no-ops with zero overhead (OTEL API default behavior).
"""
import logging
from typing import Optional

from opentelemetry import context as otel_context, trace
from opentelemetry.propagate import extract, inject

from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False

# ---------------------------------------------------------------------------
# Vendor-specific OTel attribute presets
# ---------------------------------------------------------------------------
_VENDOR_PRESETS: dict[str, dict[str, str]] = {
    "langwatch": {
        "thread_id": "langwatch.thread.id",
        "user_id": "langwatch.user.id",
        "span_type": "langwatch.span.type",
        "input": "langwatch.input",
        "output": "langwatch.output",
        "labels": "langwatch.labels",
    },
    "langfuse": {
        "thread_id": "langfuse.session.id",
        "user_id": "langfuse.user.id",
        "span_type": "langfuse.span.type",
        "input": "langfuse.input",
        "output": "langfuse.output",
        "labels": "langfuse.tags",
    },
    "generic_otel": {
        "thread_id": "gen_ai.thread.id",
        "user_id": "gen_ai.user.id",
        "span_type": "gen_ai.span.type",
        "input": "gen_ai.input",
        "output": "gen_ai.output",
        "labels": "gen_ai.labels",
    },
}

_active_preset: dict[str, str] = _VENDOR_PRESETS.get(
    settings.otel_vendor, _VENDOR_PRESETS["langwatch"]
)


def otel_attr(role: str) -> str:
    """Return vendor-specific OTel attribute name for a semantic role.

    Roles: thread_id, user_id, span_type, input, output, labels
    """
    return _active_preset[role]


def init_telemetry() -> None:
    """Initialize OTEL TracerProvider with OTLP HTTP exporter.

    Safe to call multiple times (idempotent). No-ops if disabled.
    Must be called once per process (backend, each worker).
    """
    global _initialized
    if _initialized or not settings.otel_enabled or not settings.otel_exporter_endpoint:
        return

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers: dict[str, str] = {}
        if settings.otel_exporter_headers:
            for pair in settings.otel_exporter_headers.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    headers[k.strip()] = v.strip()

        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_endpoint,
            headers=headers,
        )

        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _initialized = True
        logger.info(
            f"OTEL initialized: endpoint={settings.otel_exporter_endpoint}, "
            f"service={settings.otel_service_name}"
        )
    except Exception as e:
        logger.error(f"Failed to initialize OTEL: {e}")


def get_tracer(name: str = "sinas") -> trace.Tracer:
    """Return a tracer. Returns NoOp tracer when OTEL is disabled."""
    return trace.get_tracer(name)


def inject_trace_context() -> dict[str, str]:
    """Serialize current span context into a plain dict for queue propagation."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict[str, str]) -> Optional[otel_context.Context]:
    """Deserialize trace context from a dict (received from queue kwargs)."""
    if not carrier:
        return None
    return extract(carrier=carrier)
