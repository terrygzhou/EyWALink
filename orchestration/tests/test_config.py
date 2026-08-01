"""Tests for YAML config parsing."""

from __future__ import annotations

import pytest

from eywalink_orchestration.config import ConfigError, get_llm_config, load_config


def test_load_config_merges_defaults(tmp_path):
    cfg_path = tmp_path / "pipeline-config.yaml"
    cfg_path.write_text(
        "pipeline:\n  name: demo\nllm:\n  base_url: http://localhost:8080/v1\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg["pipeline"]["name"] == "demo"
    # defaults merged
    assert cfg["llm"]["max_tokens"] == 32768
    assert cfg["gates"]["max_rework"] == 2
    assert cfg["tools"]["mcp_servers"] == []


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_load_config_malformed_yaml(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("pipeline: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_get_llm_config(tmp_path):
    cfg_path = tmp_path / "pipeline-config.yaml"
    cfg_path.write_text("llm:\n  model: Qwen3.6-35B-A3B\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    llm_cfg = get_llm_config(cfg)
    assert llm_cfg["model"] == "Qwen3.6-35B-A3B"
