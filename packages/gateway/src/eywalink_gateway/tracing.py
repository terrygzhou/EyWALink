"""OpenTelemetry tracing setup.

Sends OTLP spans to the otel-collector (which fans out to Phoenix and
Prometheus/Loki). Disabled gracefully when GATEWAY_OTEL_ENDPOINT is
unreachable — the app must never crash because observability is down.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

try:  # instrumentation packages are optional at runtime
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    _INSTR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _INSTR_AVAILABLE = False


def setup_tracing(service_name: str, endpoint: str | None) -> None:
    """Initialize the global tracer provider. Safe to call once."""
    if not endpoint:
        logger.info("OTel disabled (no endpoint)")
        return

    resource = Resource.create({"service.name": service_name})
    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("OTel exporter configured at %s", endpoint)
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to configure OTel exporter: %s", exc)


def instrument_app(app) -> None:
    if _INSTR_AVAILABLE:
        try:
            FastAPIInstrumentor.instrument_app(app)
            HTTPXClientInstrumentor().instrument()
        except Exception:  # pragma: no cover
            logger.warning("Instrumentation failed; continuing without it")
