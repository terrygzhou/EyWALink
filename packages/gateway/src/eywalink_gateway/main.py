"""FastAPI application entrypoint for the EyWALink gateway."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import get_settings
from .providers import ProviderPool
from .router import router
from .tracing import instrument_app, setup_tracing
from .vectors import router as vectors_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.pool = ProviderPool(
        [(name, settings.providers[name]) for name in settings.ordered_provider_names]
    )
    app.state.client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=settings.max_concurrent),
    )
    app.state.qdrant = None
    if settings.qdrant_url:
        try:
            from qdrant_client import QdrantClient

            app.state.qdrant = QdrantClient(
                url=settings.qdrant_url, timeout=int(settings.qdrant_timeout)
            )
            logger.info("Qdrant client configured at %s", settings.qdrant_url)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to configure Qdrant client: %s", exc)
    setup_tracing(settings.app_name, settings.otel_endpoint)
    logger.info(
        "Gateway ready. Fallback chain: %s",
        settings.ordered_provider_names,
    )
    yield
    await app.state.client.aclose()
    if app.state.qdrant is not None:
        app.state.qdrant.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="EyWALink Private AI Gateway",
        version="0.1.0",
        description="Zero lock-in self-hosted AI gateway with multi-model fallback.",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(vectors_router)
    instrument_app(app)

    settings = get_settings()
    if settings.metrics_enabled:

        @app.get("/metrics")
        async def metrics() -> Response:
            # Serve directly at /metrics (no trailing-slash redirect), matching
            # the prometheus.yml scrape path.
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/")
    async def root():
        return {
            "service": settings.app_name,
            "docs": "/docs",
            "metrics": "/metrics",
            "health": "/v1/health",
        }

    return app


app = create_app()
