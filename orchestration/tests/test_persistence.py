"""Tests for state persistence round-trip and state reducers."""

from __future__ import annotations

import json

from eywalink_orchestration.persistence import (
    load_state,
    save_state,
    state_file_path,
)


def test_nonexistent_load_returns_none(tmp_path):
    assert load_state(tmp_path / "missing.json") is None


def test_save_load_roundtrip(tmp_path):
    state = {
        "project_name": "demo",
        "objective": "Build a demo",
        "requirements_doc": "REQ",
        "architecture_doc": "ARCH",
        "code_files": {"main.py": "print(1)"},
        "qa_report": "OK",
        "req_approved": True,
        "qa_passed": True,
        "rework_count": 0,
        "human_feedback": "",
    }
    path = tmp_path / "sub" / "pipeline-status.json"
    save_state(path, state)
    assert path.exists()

    loaded = load_state(path)
    assert loaded == state


def test_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_state(path) is None


def test_state_file_path_resolution(tmp_path):
    p = state_file_path(tmp_path, "demo")
    assert p.name == "demo-status.json"


def test_schema_version_present(tmp_path):
    path = tmp_path / "pipeline-status.json"
    save_state(path, {"a": 1})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
