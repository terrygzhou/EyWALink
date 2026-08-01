"""Tests for pipeline state: reducers and round-trip persistence."""

from __future__ import annotations

from eywalink_orchestration.persistence import load_state, save_state
from eywalink_orchestration.state import (
    PipelineState,
    make_initial_state,
    merge_artifacts,
)


def test_merge_artifacts_right_wins_per_key() -> None:
    left = {"spec": {"title": "A"}, "arch": {"ok": True}}
    right = {"arch": {"ok": False}, "code": {"files": ["a.py"]}}
    merged = merge_artifacts(left, right)
    assert merged["spec"]["title"] == "A"  # untouched key preserved
    assert merged["arch"]["ok"] is False  # right wins on collision
    assert merged["code"]["files"] == ["a.py"]


def test_initial_state_shape() -> None:
    state = make_initial_state("Build a demo", pipeline_name="demo")
    assert state["phase"] == "init"
    assert state["status"] == "pending"
    assert state["messages"] == []
    assert state["artifacts"] == {}
    assert state["tokens_used"] == 0


def test_state_round_trip(tmp_path) -> None:
    """Write, reload, verify — the core durability guarantee."""
    state: PipelineState = {
        **make_initial_state("Round trip", pipeline_name="rt"),
        "phase": "implementation",
        "status": "running",
        "messages": [{"role": "user", "content": "hi"}],
        "artifacts": {"code": {"files": ["main.py"]}},
        "tokens_used": 42,
        "steps_completed": 3,
    }

    path = tmp_path / "pipeline-status.json"
    save_state(state, path)
    assert path.exists()

    reloaded = load_state(path)
    assert reloaded is not None
    assert reloaded["goal"] == "Round trip"
    assert reloaded["pipeline_name"] == "rt"
    assert reloaded["phase"] == "implementation"
    assert reloaded["status"] == "running"
    assert reloaded["messages"] == [{"role": "user", "content": "hi"}]
    assert reloaded["artifacts"]["code"]["files"] == ["main.py"]
    assert reloaded["tokens_used"] == 42
    assert reloaded["steps_completed"] == 3


def test_load_missing_state_returns_none(tmp_path) -> None:
    assert load_state(tmp_path / "nope.json") is None


def test_save_is_atomic(tmp_path) -> None:
    """No leftover .tmp file after a successful save."""
    state = make_initial_state("Atomic", pipeline_name="a")
    path = save_state(state, tmp_path / "status.json")
    assert not path.with_suffix(".json.tmp").exists()
