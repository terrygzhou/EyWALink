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
        ValueError: if the parsed root is not a mapping, or a required key
            (``pipeline.llm.base_url`` / ``pipeline.llm.model``) is missing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Pipeline config not found: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Pipeline config root must be a mapping, got {type(raw).__name__}")

    # Required keys are validated on the USER-provided config (pre-merge), so a
    # missing llm endpoint is an error instead of being silently filled by the
    # default. Optional knobs (temperature, timeouts) still merge from defaults.
    _validate_required(raw, str(p))

    # Deep-merge user config over defaults so partial files still work.
    merged = _deep_merge(DEFAULT_CONFIG, raw)
    _validate_types(merged, str(p))
    return merged


def _validate_required(cfg: dict[str, Any], source: str) -> None:
    """Check the fields that must come from the user's config file."""
    pipeline = cfg.get("pipeline") or {}
    llm = pipeline.get("llm") or {}
    for key in ("base_url", "model"):
        if not llm.get(key):
            raise ValueError(f"pipeline.llm.{key} is required in {source}")


def _validate_types(cfg: dict[str, Any], source: str) -> None:
    """Type-check the merged config."""
    pipeline = cfg.get("pipeline", {})
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
