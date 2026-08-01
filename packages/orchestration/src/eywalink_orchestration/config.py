"""YAML pipeline configuration loader.

Loads a ``pipeline-config.yaml`` into a validated dict with sane defaults so
pipelines can be declared in plain YAML and executed without code changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "pipeline": {
        "name": "default",
        "max_steps": 100,
        "sequential": True,
        "llm": {
            "base_url": "http://localhost:8080",
            "model": "Qwen3.6-27B-NVFP4",
            "temperature": 0.2,
            "max_tokens": 4096,
            "timeout_read": 600,
        },
    },
    "agents": {},
    "persistence": {
        "status_file": "pipeline-status.json",
    },
}


def load_pipeline_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a pipeline YAML config, merging with defaults.

    Raises:
        FileNotFoundError: if the file does not exist.
        yaml.YAMLError: if the YAML is invalid.
        ValueError: if the parsed root is not a mapping.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Pipeline config not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Pipeline config root must be a mapping, got {type(raw).__name__}")

    # Required keys must be present in the user's YAML — defaults must not
    # silently mask a config that forgot to declare its LLM endpoint.
    _validate_required(raw, str(p))

    # Deep-merge user config over defaults so partial files still work.
    merged = _deep_merge(DEFAULT_CONFIG, raw)
    _validate(merged, str(p))
    return merged


def _validate_required(cfg: dict[str, Any], source: str) -> None:
    """Raise if required user-facing keys are missing from the raw config.

    ``pipeline.llm.base_url`` and ``pipeline.llm.model`` are load-bearing:
    they decide which local model server the pipeline talks to, so they must
    be declared explicitly instead of inherited from defaults.
    """
    llm = (cfg.get("pipeline") or {}).get("llm") or {}
    for key in ("base_url", "model"):
        if not llm.get(key):
            raise ValueError(f"pipeline.llm.{key} is required in {source}")


def _validate(cfg: dict[str, Any], source: str) -> None:
    pipeline = cfg.get("pipeline", {})
    llm = pipeline.get("llm", {})
    for key in ("base_url", "model"):
        if not llm.get(key):
            raise ValueError(f"pipeline.llm.{key} is required in {source}")
    if not isinstance(pipeline.get("max_steps", 100), int):
        raise ValueError(f"pipeline.max_steps must be an int in {source}")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (returns a new dict)."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
