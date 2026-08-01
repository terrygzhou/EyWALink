# ─── EyWALink Private AI Gateway ─────────────────────────────────────────
# FastAPI application layer: OpenAI-compatible chat completions with
# automatic multi-model fallback (SGLang -> vLLM -> Ollama), Qdrant vector
# search, Prometheus metrics, and OpenTelemetry tracing.
#
# Build:   docker build -t eywalink-gateway .
# Run:     see packages/deploy/docker-compose.yml (service: gateway)
# ─────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY src/ ./src/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/health', timeout=3)"

CMD ["uvicorn", "src.eywalink_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
