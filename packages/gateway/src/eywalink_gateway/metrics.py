"""Prometheus metrics for the gateway.

Exposed at /metrics via prometheus-client. The metrics below cover the
AIOps essentials: request volume, latency, fallback events, and tokens.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "eywalink_requests_total",
    "Total chat completion requests handled",
    ["provider", "status"],
)

FALLBACKS_TOTAL = Counter(
    "eywalink_fallbacks_total",
    "Requests that failed over from one provider to another",
    ["from_provider", "to_provider", "reason"],
)

LATENCY_SECONDS = Histogram(
    "eywalink_request_duration_seconds",
    "End-to-end latency of chat completion requests",
    ["provider"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

TOKENS_TOTAL = Counter(
    "eywalink_tokens_total",
    "Tokens generated across all providers",
    ["provider", "kind"],  # kind: prompt | completion
)

PROVIDER_UP = Gauge(
    "eywalink_provider_up",
    "Whether a provider is currently considered healthy",
    ["provider"],
)

PROVIDER_CIRCUIT_OPEN = Gauge(
    "eywalink_provider_circuit_open",
    "1 when the circuit breaker for a provider is open (tripped)",
    ["provider"],
)

INFLIGHT = Gauge(
    "eywalink_requests_inflight",
    "Number of requests currently being processed",
)
