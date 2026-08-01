"""Tests for the human-in-the-loop review workflow.

Uses the same mocked-LLM technique as test_agents.py (httpx.MockTransport
dispatching canned JSON per agent role), then drives the pause/resume cycle
through LangGraph's checkpointer + ``Command(resume=...)``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from eywalink_orchestration import (
    AgentContext,
    LLMClient,
    build_reviewed_pipeline_graph,
    make_initial_state,
    review_resume_approve,
    review_resume_changes,
)

GOAL = "Build a CLI that summarizes markdown files"

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


def _handler(canned: dict[str, dict]) -> httpx.MockTransport:
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
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]}, request=request)

    return httpx.MockTransport(handler)


def _ctx(canned: dict[str, dict], workdir=None) -> AgentContext:
    llm = LLMClient("http://llm:8080", "test-model", transport=_handler(canned))
    return AgentContext(llm=llm, workdir=workdir)


def _thread(thread_id: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


@pytest.mark.asyncio
async def test_pipeline_pauses_for_human_review(tmp_path) -> None:
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    graph = build_reviewed_pipeline_graph(ctx)

    result = await graph.ainvoke(make_initial_state(GOAL), config=_thread())

    # Execution suspends at the review gate: interrupt payload surfaced.
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "human_review"
    assert payload["review_after"] == "agent_coder"
    assert len(payload["summary"]["code_files"]) == 2
    # The Coder already wrote files before the pause.
    assert (tmp_path / "src/summarize.py").exists()


@pytest.mark.asyncio
async def test_approve_resumes_to_qa(tmp_path) -> None:
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    graph = build_reviewed_pipeline_graph(ctx)
    await graph.ainvoke(make_initial_state(GOAL), config=_thread())

    final = await graph.ainvoke(review_resume_approve(), config=_thread())

    assert "__interrupt__" not in final
    assert final["status"] == "done"
    assert final["phase"] == "qa"
    assert final["artifacts"]["qa_report"]["passed"] is True
    assert final["reviews_used"] == 1


@pytest.mark.asyncio
async def test_request_changes_loops_back_to_coder(tmp_path) -> None:
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    graph = build_reviewed_pipeline_graph(ctx)
    await graph.ainvoke(make_initial_state(GOAL), config=_thread())

    # Human asks for changes -> coder runs again -> review pauses again.
    result = await graph.ainvoke(
        review_resume_changes("Add type hints to all functions."), config=_thread()
    )

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "human_review"
    assert result["reviews_used"] == 1  # one review round consumed
    # Coder re-ran after feedback: pm+arch+coder (1st pass) + coder (2nd pass).
    assert result["steps_completed"] == 4

    # Approve on the second round -> pipeline completes.
    final = await graph.ainvoke(review_resume_approve(), config=_thread())
    assert final["status"] == "done"
    assert final["reviews_used"] == 2


@pytest.mark.asyncio
async def test_invalid_review_after_raises(tmp_path) -> None:
    ctx = _ctx(CANNED_OK, workdir=tmp_path)
    with pytest.raises(ValueError, match="review_after"):
        build_reviewed_pipeline_graph(ctx, review_after="agent_nope")
