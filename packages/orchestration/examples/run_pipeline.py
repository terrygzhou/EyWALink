"""End-to-end multi-agent pipeline demo (PM -> Architect -> Coder -> QA -> review gate).

Runs the full LangGraph pipeline with a human-in-the-loop review gate:

- Without flags: connects to a real local model server (default
  http://localhost:8080, SGLang/vLLM/Ollama). Configure via
  examples/pipeline-config.yaml or CLI flags.
- With ``--mock``: uses canned LLM responses (no server needed) so the
  flow can be exercised anywhere.

The demo pauses at the review gate; use ``--approve`` to resume it
programmatically, or omit it to see the interrupt payload that a human
would approve.

Usage:
    uv run python examples/run_pipeline.py --goal "Build a markdown summarizer CLI" --mock --approve
    uv run python examples/run_pipeline.py --config examples/pipeline-config.yaml --approve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from eywalink_orchestration import (
    AgentContext,
    LLMClient,
    build_reviewed_pipeline_graph,
    load_pipeline_config,
    make_initial_state,
    review_resume_approve,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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


def _mock_transport() -> httpx.MockTransport:
    """Canned LLM responses per agent role (no server needed)."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sys_prompt = payload["messages"][0]["content"]
        if "Product Manager" in sys_prompt:
            body = CANNED_OK["pm"]
        elif "Architect agent" in sys_prompt:
            body = CANNED_OK["architect"]
        elif "Coder agent" in sys_prompt:
            body = CANNED_OK["coder"]
        elif "QA agent" in sys_prompt:
            body = CANNED_OK["qa"]
        else:
            body = {"error": "unknown role"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(body)}}]},
            request=request,
        )

    return httpx.MockTransport(handler)


def _build_context(args: argparse.Namespace) -> AgentContext:
    if args.mock:
        llm = LLMClient("http://mock.invalid", "mock-model", transport=_mock_transport())
        workdir = REPO_ROOT / "generated" / "demo"
        return AgentContext(llm=llm, workdir=workdir)

    if args.config:
        config = load_pipeline_config(args.config)
        return AgentContext.from_config(config, workdir=REPO_ROOT / "generated" / "demo")

    llm = LLMClient(args.base_url, args.model)
    return AgentContext(llm=llm, workdir=REPO_ROOT / "generated" / "demo")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default="Build a CLI that summarizes markdown files")
    parser.add_argument("--mock", action="store_true", help="use canned LLM responses (no server)")
    parser.add_argument("--config", help="pipeline YAML config path")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--model", default="Qwen3.6-27B-NVFP4")
    parser.add_argument("--approve", action="store_true", help="auto-approve at the review gate")
    args = parser.parse_args()

    ctx = _build_context(args)
    graph = build_reviewed_pipeline_graph(ctx)
    thread = {"configurable": {"thread_id": "demo-run-1"}}

    print(f"==> Starting pipeline: {args.goal!r}")
    result = await graph.ainvoke(make_initial_state(args.goal), config=thread)

    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print("==> PAUSED at human review gate")
        print(f"    phase={payload['phase']} code_files={payload['summary']['code_files']}")
        if not args.approve:
            print("    Resume with: --approve")
        else:
            print("==> Approving...")
            result = await graph.ainvoke(review_resume_approve(), config=thread)

    print("==> Final state")
    print(f"    phase={result.get('phase')} status={result.get('status')}")
    print(f"    steps={result.get('steps_completed')} reviews={result.get('reviews_used', 0)}")
    qa_report = (result.get("artifacts") or {}).get("qa_report") or {}
    print(f"    qa_report.passed={qa_report.get('passed')}")

    files = ((result.get("artifacts") or {}).get("code") or {}).get("files") or []
    print(f"    generated_files={[f.get('path') for f in files]}")
    return 0 if result.get("status") in ("done", "running", "blocked") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
