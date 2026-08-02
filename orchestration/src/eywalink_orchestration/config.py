"""YAML pipeline configuration parser.

Single pipeline-config.yaml drives the whole pipeline (deterministic,
version-controlled, human-readable). Loaded into a plain dict consumed by
the graph builder, LLM client, MCP tool layer, and persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


DEFAULT_CONFIG: dict[str, Any] = {
    "pipeline": {
        "name": "unnamed-pipeline",
        "objective": "",
    },
    "llm": {
        "base_url": "http://localhost:8080/v1",
        "model": "Qwen3.6-35B-A3B",
        "api_key": "",
        "max_tokens": 32768,
        "temperature": 0.7,
        "read_timeout": 600,
        "connect_timeout": 30,
    },
    "state": {
        "file": "pipeline-status.json",
        "persist_artifacts": True,
    },
    "tools": {
        "mcp_servers": [],
    },
    "gates": {
        "max_rework": 2,
    },
}


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file, deep-merged over defaults.

    Raises ConfigError on unreadable file or malformed YAML.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {p}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")
    return _deep_merge(DEFAULT_CONFIG, raw)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get_llm_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("llm", {}))


def get_state_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("state", {}))


def get_tool_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("tools", {}))
