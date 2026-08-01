"""Contract tests for the high-level pipeline API facade.

Validates the documented surface (docs/api-contracts.md §3-ish service
operations): run_pipeline / get_status / resume / get_artifacts. Uses the
mocked-LLM transport so the tests run without a model server.
"""

from __future__ import annotations

import json

import httpx
import pytest

from eywalink_orchestration import (
    get_artifacts,
    get_status,
    resume,
    run_pipeline,
)
from eywalink_orchestration.config import DEFAULT_CONFIG

GOAL = "Build a CLI that summarizes markdown files"

CANNED_OK = {
    "pm": {
        "title": "Markdown Summary CLI",
        "summary": "A CLI tool that summarizes markdown files.",
        "requirements": [
            {"id": "R1", "description": "Accept a file path argument", "priority": "must"},
        ],
        "acceptance_criteria": ["CLI exits 0 on valid input"],
        "out_of_scope": ["Web UI"],
    },
    "architect": {
        "overview": "Single-module Python CLI.",
        "components": [{"name": "cli", "responsibility": "parse args", "tech": "argparse"}],
        "data_flow": ["read file", "summarize", "print"],
        "tech_stack": {"language": "python", "framework": "stdlib", "storage": "none"},
        "file_plan": [
            {"path": "src/summarize.py", "purpose": "entry point", "language": "python"},
            {"path": "src/summarizer.py", "purpose": "summarization logic", "language": "python"},
        ],
        "risks": ["large files"],
    },
    "coder": {
        "path": "src/summarize.py",
        "language": "python",
        "content": "def main():\n    print('ok')\n\nif __name__ == '__main__':\n    main()\n",
    },
    "qa": {
        "test_plan": [{"id": "T1", "scenario": "runs", "type": "unit"}],
        "test_files": [
            {"path": "tests/test_summarize.py", "content": "def test_main():\n    assert True\n"}
        ],
    },
}


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = next((m["content"] for m in body["messages"] if m["role"] == "system"), "")
        for key, payload in CANNED_OK.items():
            if key in system.lower():
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    return httpx.MockTransport(handler)


def _config(tmp_path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    cfg["pipeline"]["llm"]["base_url"] = "http://llm:8080"
    cfg["pipeline"]["llm"]["model"] = "test-model"
    return cfg


@pytest.mark.asyncio
async def test_run_pipeline_full_and_get_status(tmp_path) -> None:
    status_file = tmp_path / "contract-status.json"
    cfg = _config(tmp_path)

    result = await run_pipeline(
        GOAL,
        cfg,
        workdir=tmp_path,
        status_file=status_file,
        review=False,
        transport=_mock_transport(),
    )

    # run_pipeline returns the durable state with artifacts accumulated.
    assert result["status"] == "done"
    assert result["phase"] == "qa"
    assert result["steps_completed"] == 4
    assert result["artifacts"]["qa_report"]["passed"] is True

    # Contract: status file is the durable JSON record.
    assert status_file.exists()
    on_disk = json.loads(status_file.read_text(encoding="utf-8"))
    assert on_disk["goal"] == GOAL
    assert on_disk["status"] == "done"

    # get_status reads it back in the documented shape.
    status = get_status(status_file)
    assert status["status"] == "done"
    assert status["phase"] == "qa"
    assert status["steps_completed"] == 4

    # get_artifacts returns the artifact dict.
    artifacts = get_artifacts(status_file)
    assert artifacts is not None
    assert set(artifacts) >= {"spec", "architecture", "code", "qa_report"}


@pytest.mark.asyncio
async def test_get_status_not_found(tmp_path) -> None:
    status = get_status(tmp_path / "missing.json")
    assert status["status"] == "not_found"
    assert status["goal"] is None


@pytest.mark.asyncio
async def test_run_pipeline_with_yaml_config(tmp_path) -> None:
    from eywalink_orchestration import load_pipeline_config

    cfg_path = tmp_path / "pipeline.yaml"
    cfg_path.write_text(
        "pipeline:\n"
        "  name: contract-test\n"
        "  llm:\n"
        "    base_url: http://llm:8080\n"
        "    model: test-model\n",
        encoding="utf-8",
    )
    cfg = load_pipeline_config(cfg_path)

    status_file = tmp_path / "yaml-status.json"
    result = await run_pipeline(
        GOAL, cfg, workdir=tmp_path, status_file=status_file, review=False, transport=_mock_transport()
    )
    assert result["status"] == "done"


@pytest.mark.asyncio
async def test_run_pipeline_review_gate_and_resume(tmp_path) -> None:
    status_file = tmp_path / "review-status.json"
    cfg = _config(tmp_path)

    # Starts and pauses at the human review gate (review=True default).
    paused = await run_pipeline(
        GOAL,
        cfg,
        workdir=tmp_path,
        status_file=status_file,
        thread_id="contract-review",
        transport=_mock_transport(),
    )
    assert paused["status"] == "running"
    assert "__interrupt__" in paused

    # Human approves via the resume operation.
    resumed = await resume(
        "approve",
        config=cfg,
        workdir=tmp_path,
        status_file=status_file,
        thread_id="contract-review",
        transport=_mock_transport(),
    )
    assert resumed["status"] == "done"
    assert resumed["artifacts"]["qa_report"]["passed"] is True

    # get_status reflects the completed run.
    assert get_status(status_file)["status"] == "done"


@pytest.mark.asyncio
async def test_resume_requires_feedback_for_changes(tmp_path) -> None:
    cfg = _config(tmp_path)
    with pytest.raises(ValueError):
        await resume("request_changes", config=cfg, workdir=tmp_path, thread_id="nope", transport=_mock_transport())


@pytest.mark.asyncio
async def test_resume_requires_config(tmp_path) -> None:
    with pytest.raises(ValueError):
        await resume("approve", workdir=tmp_path, thread_id="nope")
