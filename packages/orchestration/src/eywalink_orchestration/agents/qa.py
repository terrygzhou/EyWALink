"""agent_qa — test generation and validation.

The QA agent generates tests for the Coder's output and validates it. Two
halves:

1. **Test generation**: asks the LLM for a test plan + test files for the
   generated code (``artifacts["tests"]``).
2. **Static validation**: cheap, deterministic checks run locally without
   the LLM — file presence, non-empty content, balanced Python delimiters
   for ``.py`` files. This catches broken generations immediately and is
   zero-cost on budget-friendly infra.

Validation results are recorded in ``artifacts["qa_report"]`` with a
``passed`` boolean the pipeline can branch on.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..llm import LLMError
from ..state import PipelineState
from .base import AgentContext, fail, record_message, record_output

logger = logging.getLogger(__name__)

TEST_KEYS = ("test_plan", "test_files")

SYSTEM_PROMPT = """You are the QA agent in an autonomous software pipeline.
Given the generated code files, produce a test plan and test files.

Respond with a JSON object ONLY, using exactly this shape:
{
  "test_plan": [
    {"id": "T1", "scenario": "what is tested", "type": "unit|integration"}
  ],
  "test_files": [
    {"path": "tests/test_app.py", "content": "complete test source code"}
  ]
}
Do not explain. Do not include markdown fences in "content"."""


def _validate_file(path: str, content: str) -> list[str]:
    """Run cheap static checks on a generated file. Returns issue strings."""
    issues: list[str] = []
    if not content.strip():
        issues.append(f"{path}: empty content")
    if path.endswith(".py"):
        # Balanced brackets is a cheap syntax sanity check that catches
        # truncation (a common failure mode for local LLM generations).
        for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
            if content.count(open_ch) != content.count(close_ch):
                issues.append(f"{path}: unbalanced {open_ch}{close_ch}")
    if path.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            issues.append(f"{path}: invalid JSON ({exc})")
    return issues


def _validate_code(code: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Validate generated files; returns (passed, checks)."""
    files = code.get("files") or []
    checks: list[dict[str, Any]] = []
    passed = True
    for f in files:
        issues = _validate_file(str(f.get("path", "?")), str(f.get("content", "")))
        ok = not issues
        passed = passed and ok
        checks.append(
            {
                "path": f.get("path"),
                "language": f.get("language"),
                "passed": ok,
                "issues": issues,
            }
        )
    return passed, checks


async def agent_qa(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Test generation + validation node."""
    artifacts = state.get("artifacts") or {}
    code = artifacts.get("code") or {}
    files = code.get("files") or []

    # --- Half 1: local static validation (deterministic, no LLM) -----------
    passed, checks = _validate_code(code)

    # --- Half 2: LLM test generation (only if we have code to test) --------
    tests: dict[str, Any] = {}
    test_gen_issues: list[str] = []
    if files:
        code_blob = json.dumps(files, default=str)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Project goal:\n{state.get('goal', '')}\n\n"
                    f"Generated code files:\n{code_blob}\n\n"
                    "Generate a test plan and test files."
                ),
            },
        ]
        try:
            raw = await ctx.llm.complete_json(messages, max_tokens=4096)
            tests = {k: raw.get(k) for k in TEST_KEYS if k in raw}
        except LLMError as exc:
            test_gen_issues.append(str(exc))
            logger.warning("qa test generation failed: %s", exc)

    # Write test files to disk when a workdir is configured.
    if ctx.workdir is not None:
        for tf in tests.get("test_files") or []:
            path = str(tf.get("path", "tests/test_generated.py"))
            target = (ctx.workdir / path.lstrip("/")).resolve()
            if ctx.workdir.resolve() in target.parents or target.parent == ctx.workdir.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(tf.get("content", "")), encoding="utf-8")

    qa_report = {
        "passed": passed,
        "checks": checks,
        "test_generation_issues": test_gen_issues,
        "files_validated": len(files),
    }

    return {
        "phase": "qa",
        "status": "done" if passed else "blocked",
        "artifacts": {
            "tests": tests,
            "qa_report": qa_report,
        },
        "messages": [
            record_message(
                f"QA: {sum(1 for c in checks if c['passed'])}/{len(checks)} files passed validation"
            )
        ],
        "agent_outputs": [
            record_output(
                "agent_qa",
                "qa_report",
                passed=passed,
                files_validated=len(files),
            )
        ],
        "steps_completed": 1,
    }
