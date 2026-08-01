"""Tests for the four core agent nodes and the sequential pipeline graph.

The LLM is mocked with an ``httpx.MockTransport`` that dispatches canned
JSON responses by inspecting which agent's system prompt is in the request —
no live model server needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from eywalink_orchestration import (
    AgentContext,
    LLMClient,
    build_pipeline_graph,
    make_initial_state,
)
from eywalink_orchestration.agents import agent_architect, agent_coder, agent_pm, agent_qa
from eywalink_orchestration.state import PipelineState

GOAL = "Build a CLI that summarizes markdown files"


# ---------------------------------------------------------------------------
# Mock LLM server: dispatches canned JSON per agent role.
# ---------------------------------------------------------------------------

def _handler(canned: dict[str, dict]) -> httpx.MockTransport:
    """Build a mock transport.

    ``canned["pm"]`` may be set to the sentinel ``{"__http_status__": 500}``
    to simulate an LLM server failure for the PM role. Otherwise each role
    returns its canned JSON object.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sys_prompt = payload["messages"][0]["content"]
        if "Product Manager" in sys_prompt:
            body = canned["pm"]
        elif "Architect agent" in sys_prompt:
            body = canned["architect"]
        elif "Coder agent" in sys_prompt:
            body = canned["coder"]
        elif "QA agent" in sys_prompt:
            body = canned["qa"]
        else:
            body = {"error": "unknown role"}
        if isinstance(body, dict) and body.get("__http_status__"):
            return httpx.Response(int(body["__http_status__"]), json={"error": "boom"}, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]}, request=request)

    return httpx.MockTransport(handler)


class _RequestCounter:
    """Counts LLM requests seen by the transport."""

    def __init__(self) -> None:
        self.count = 0

    def make(self, canned: dict[str, dict]) -> httpx.MockTransport:
        def inner(request: httpx.Request) -> httpx.Response:
            self.count += 1
            payload = json.loads(request.content)
            sys_prompt = payload["messages"][0]["content"]
            if "Product Manager" in sys_prompt:
                body = canned["pm"]
            elif "Architect agent" in sys_prompt:
                body = canned["architect"]
            elif "Coder agent" in sys_prompt:
                body = canned["coder"]
            elif "QA agent" in sys_prompt:
                body = canned["qa"]
            else:
                body = {"error": "unknown role"}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]}, request=request)

        return httpx.MockTransport(inner)


def _ctx(canned: dict[str, dict], workdir=None) -> AgentContext:
    llm = LLMClient("http://llm:8080", "test-model", transport=_handler(canned))
    return AgentContext(llm=llm, workdir=workdir)


CANNED_OK = {
    "pm": {
        "title": "Markdown Summary CLI",
        "summary": "A CLI tool that summarizes markdown files.",
        "requirements": [
            {"id": "R1", "description": "Accept a file path argument", "priority": "must"},
            {"id": "R2", "description": "Print a concise summary", "priority": "must"},
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


# ---------------------------------------------------------------------------
# Per-agent node tests.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_pm_generates_spec() -> None:
    state = make_initial_state(GOAL)
    partial = await agent_pm(state, _ctx(CANNED_OK))
    assert partial["phase"] == "requirements"
    assert partial["artifacts"]["spec"]["title"] == "Markdown Summary CLI"
    assert len(partial["artifacts"]["spec"]["requirements"]) == 2
    assert partial["agent_outputs"][0]["agent"] == "agent_pm"
    assert partial["steps_completed"] == 1


@pytest.mark.asyncio
async def test_agent_pm_fails_on_empty_goal() -> None:
    state = make_initial_state("   ")
    partial = await agent_pm(state, _ctx(CANNED_OK))
    assert partial["status"] == "failed"
    assert "empty" in partial["error"]


@pytest.mark.asyncio
async def test_agent_architect_produces_file_plan() -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {"spec": CANNED_OK["pm"]},
    }
    partial = await agent_architect(state, _ctx(CANNED_OK))
    assert partial["phase"] == "architecture"
    plan = partial["artifacts"]["architecture"]["file_plan"]
    assert len(plan) == 2
    assert plan[0]["path"] == "src/summarize.py"


@pytest.mark.asyncio
async def test_agent_coder_writes_files_sequentially(tmp_path) -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {
            "spec": CANNED_OK["pm"],
            "architecture": CANNED_OK["architect"],
        },
    }
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    partial = await agent_coder(state, ctx)
    assert partial["phase"] == "implementation"
    code = partial["artifacts"]["code"]
    assert len(code["files"]) == 2  # two chunks, one file each (chunk_size=1)
    written = (tmp_path / "src/summarize.py")
    assert written.exists()
    assert "def main()" in written.read_text()


@pytest.mark.asyncio
async def test_agent_coder_uses_chunk_size_from_config(tmp_path) -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {
            "spec": CANNED_OK["pm"],
            "architecture": CANNED_OK["architect"],
        },
    }
    # Canned coder payload returns BOTH planned files (list form), as a
    # well-behaved model does for a multi-file chunk request.
    canned = json.loads(json.dumps(CANNED_OK))
    canned["coder"] = [
        {
            "path": "src/summarize.py",
            "language": "python",
            "content": "def main():\n    print('ok')\n",
        },
        {
            "path": "src/summarizer.py",
            "language": "python",
            "content": "def summarize(text):\n    return text[:100]\n",
        },
    ]
    counter = _RequestCounter()
    llm = LLMClient("http://llm:8080", "test-model", transport=counter.make(canned))
    ctx = AgentContext(llm=llm, workdir=tmp_path)
    ctx.config["agents"] = {"coder": {"chunk_size": 2}}

    partial = await agent_coder(state, ctx)
    # chunk_size=2 -> both files requested in ONE sequential LLM call
    # (vs 2 calls with the default chunk_size=1 in the test above).
    assert counter.count == 1
    assert len(partial["artifacts"]["code"]["files"]) == 2


@pytest.mark.asyncio
async def test_agent_coder_fails_without_file_plan() -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {"spec": CANNED_OK["pm"], "architecture": {"overview": "x"}},
    }
    partial = await agent_coder(state, _ctx(CANNED_OK))
    assert partial["status"] == "failed"


@pytest.mark.asyncio
async def test_agent_qa_validates_and_generates_tests(tmp_path) -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {
            "spec": CANNED_OK["pm"],
            "architecture": CANNED_OK["architect"],
            "code": {"files": [{"path": "src/summarize.py", "language": "python", "content": "def main():\n    pass\n"}]},
        },
    }
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    partial = await agent_qa(state, ctx)
    assert partial["phase"] == "qa"
    assert partial["status"] == "done"
    report = partial["artifacts"]["qa_report"]
    assert report["passed"] is True
    assert report["files_validated"] == 1
    assert partial["artifacts"]["tests"]["test_plan"][0]["id"] == "T1"
    assert (tmp_path / "tests/test_summarize.py").exists()


@pytest.mark.asyncio
async def test_agent_qa_blocks_on_truncated_code() -> None:
    state: PipelineState = {
        **make_initial_state(GOAL),
        "artifacts": {
            "code": {"files": [{"path": "src/broken.py", "language": "python", "content": "def main(:\n    pass\n"}]},
        },
    }
    partial = await agent_qa(state, _ctx(CANNED_OK))
    assert partial["status"] == "blocked"
    assert partial["artifacts"]["qa_report"]["passed"] is False


# ---------------------------------------------------------------------------
# End-to-end graph tests.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_runs_sequentially(tmp_path) -> None:
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    graph = build_pipeline_graph(ctx)
    final = await graph.ainvoke(make_initial_state(GOAL))

    assert final["phase"] == "qa"
    assert final["status"] == "done"
    assert final["artifacts"]["spec"]["title"] == "Markdown Summary CLI"
    assert len(final["artifacts"]["architecture"]["file_plan"]) == 2
    assert len(final["artifacts"]["code"]["files"]) == 2
    assert final["artifacts"]["qa_report"]["passed"] is True
    # Reducers accumulated across nodes:
    assert final["steps_completed"] == 4
    assert len(final["agent_outputs"]) == 4
    # Sequential chain order:
    assert [o["agent"] for o in final["agent_outputs"]] == [
        "agent_pm",
        "agent_architect",
        "agent_coder",
        "agent_qa",
    ]
    # Coder actually wrote files to disk:
    assert (tmp_path / "src/summarize.py").exists()


@pytest.mark.asyncio
async def test_pipeline_stops_on_pm_failure() -> None:
    canned = dict(CANNED_OK)
    canned["pm"] = {"__http_status__": 500}  # simulate LLM server failure
    ctx = _ctx(canned)
    graph = build_pipeline_graph(ctx)
    final = await graph.ainvoke(make_initial_state(GOAL))
    assert final["status"] == "failed"
    assert final["steps_completed"] == 1  # only PM ran
    assert "architecture" not in final.get("artifacts", {})


@pytest.mark.asyncio
async def test_pipeline_stops_when_qa_blocks() -> None:
    canned = dict(CANNED_OK)
    canned["coder"] = {"path": "src/bad.py", "language": "python", "content": "def x(:\n"}
    ctx = _ctx(canned)
    graph = build_pipeline_graph(ctx)
    final = await graph.ainvoke(make_initial_state(GOAL))
    assert final["status"] == "blocked"
    assert final["artifacts"]["qa_report"]["passed"] is False
