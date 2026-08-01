"""Tests for YAML pipeline config parsing."""

from __future__ import annotations

import pytest
import yaml

from eywalink_orchestration.config import load_pipeline_config

MINIMAL = """
pipeline:
  name: demo
  llm:
    base_url: http://localhost:8080
    model: Qwen3.6-27B-NVFP4
"""


def test_minimal_config_merges_defaults(tmp_path) -> None:
    p = tmp_path / "pipeline-config.yaml"
    p.write_text(MINIMAL, encoding="utf-8")
    cfg = load_pipeline_config(p)
    assert cfg["pipeline"]["name"] == "demo"
    assert cfg["pipeline"]["llm"]["base_url"] == "http://localhost:8080"
    assert cfg["pipeline"]["llm"]["model"] == "Qwen3.6-27B-NVFP4"
    # defaults merged in
    assert cfg["pipeline"]["max_steps"] == 100
    assert cfg["persistence"]["status_file"] == "pipeline-status.json"
    assert cfg["agents"] == {}


def test_full_config_overrides_defaults(tmp_path) -> None:
    p = tmp_path / "pipeline-config.yaml"
    p.write_text(
        """
pipeline:
  name: my-pipe
  max_steps: 5
  llm:
    base_url: http://localhost:9999
    model: local-model
    temperature: 0.7
agents:
  coder:
    chunk_size: 1
""",
        encoding="utf-8",
    )
    cfg = load_pipeline_config(p)
    assert cfg["pipeline"]["max_steps"] == 5
    assert cfg["pipeline"]["llm"]["temperature"] == 0.7
    assert cfg["agents"]["coder"]["chunk_size"] == 1


def test_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pipeline_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("pipeline: [unclosed", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_pipeline_config(p)


def test_missing_model_raises(tmp_path) -> None:
    p = tmp_path / "nollm.yaml"
    p.write_text("pipeline:\n  name: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="llm.base_url"):
        load_pipeline_config(p)
