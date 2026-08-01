"""Configuration for the EyWALink gateway.

All settings are environment-driven so the same image can be deployed
anywhere without code changes (zero lock-in).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    """Per-provider connection settings."""

    url: str
    model: str
    timeout: float = 300.0
    max_retries: int = 1
    weight: int = 1  # higher = preferred earlier in the fallback chain


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "eywalink-gateway"
    log_level: str = "INFO"

    # Fallback chain order: comma-separated provider names.
    # e.g. "sglang,vllm,ollama"
    fallback_chain: str = "sglang,vllm,ollama"

    # Primary provider (kept explicit for clarity)
    sglang_url: str = "http://sglang:8080"
    sglang_model: str = "nvidia/Qwen3.6-27B-NVFP4"
    sglang_timeout: float = 300.0

    vllm_url: str = "http://vllm:8000"
    vllm_model: str = "nvidia/Qwen3.6-27B-Text-NVFP4-MTP"
    vllm_timeout: float = 300.0

    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout: float = 120.0

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_timeout: float = 10.0

    # Observability
    otel_endpoint: str = "http://otel-collector:4317"
    metrics_enabled: bool = True

    # Request limits
    max_tokens_default: int = 4096
    max_concurrent: int = 8

    @property
    def providers(self) -> dict[str, ProviderSettings]:
        return {
            "sglang": ProviderSettings(
                url=self.sglang_url,
                model=self.sglang_model,
                timeout=self.sglang_timeout,
            ),
            "vllm": ProviderSettings(
                url=self.vllm_url,
                model=self.vllm_model,
                timeout=self.vllm_timeout,
            ),
            "ollama": ProviderSettings(
                url=self.ollama_url,
                model=self.ollama_model,
                timeout=self.ollama_timeout,
            ),
        }

    @property
    def ordered_provider_names(self) -> list[str]:
        names = [p.strip() for p in self.fallback_chain.split(",") if p.strip()]
        known = set(self.providers)
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"Unknown provider(s) in GATEWAY_FALLBACK_CHAIN: {unknown}")
        return names


@lru_cache
def get_settings() -> GatewaySettings:
    return GatewaySettings()
