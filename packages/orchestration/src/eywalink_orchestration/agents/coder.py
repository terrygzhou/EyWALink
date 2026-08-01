"""agent_coder — chunked code generation (file-by-file).

The Coder consumes the Architect's file plan and generates code **one file
at a time** (chunk size configurable via ``agents.coder.chunk_size``, default
1). It never asks the LLM for the whole codebase in one request — that keeps
requests small enough for budget-friendly local models and avoids timeouts.

Files are written under ``ctx.workdir`` when provided; the generated file
contents are recorded in ``artifacts["code"]`` so the QA agent can validate
them. Generation is strictly sequential: one LLM request at a time (VRAM
contention on local model servers — never fan out).
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

SYSTEM_PROMPT = """You are the Coder agent in an autonomous software pipeline.
You implement exactly ONE file per request, following the architecture's file plan.

Respond with a JSON object ONLY, using exactly this shape:
{
  "path": "src/app.py",
  "language": "python",
  "content": "the complete source code for this file"
}
Rules:
- Output complete, working code for the requested file only.
- Match the file plan path and language exactly.
- Do not explain the code. Do not include markdown fences in "content"."""


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split a list into sequential chunks of at most ``size``."""
    return [items[i : i + size] for i in range(0, len(items), size)]


async def agent_coder(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Chunked code generation node (file-by-file, sequential)."""
    artifacts = state.get("artifacts") or {}
    arch = artifacts.get("architecture") or {}
    spec = artifacts.get("spec") or {}
    file_plan = arch.get("file_plan") or []

    if not file_plan:
        return fail(state, "agent_coder", ValueError("architecture.file_plan is empty — nothing to code"))

    agents_cfg = ctx.config.get("agents", {})
    chunk_size = int(agents_cfg.get("coder", {}).get("chunk_size", 1) or 1)
    chunk_size = max(1, chunk_size)

    context_blob = json.dumps(
        {
            "goal": state.get("goal", ""),
            "spec": spec,
            "architecture": arch,
        },
        default=str,
    )

    generated: list[dict[str, Any]] = []
    failures: list[str] = []
    workdir: Path | None = ctx.workdir

    # Sequential execution — one LLM request at a time, per chunk of files.
    for chunk in _chunked(file_plan, chunk_size):
        user_prompt = (
            f"Project context:\n{context_blob}\n\n"
            f"Files to implement in THIS request:\n{json.dumps(chunk, default=str)}\n"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        try:
            result = await ctx.llm.complete_json(messages, max_tokens=4096)
        except LLMError as exc:
            failures.append(str(exc))
            logger.warning("coder chunk failed: %s", exc)
            continue

        # Accept either a single-file object or a multi-file response
        # ({"files": [...]} or a bare list) — chunk_size > 1 requests several
        # files in one call, so the model may return them as a list.
        entries: list[dict[str, Any]] = []
        if isinstance(result, list):
            entries = [e for e in result if isinstance(e, dict)]
        elif isinstance(result, dict) and isinstance(result.get("files"), list):
            entries = [e for e in result["files"] if isinstance(e, dict)]
        elif isinstance(result, dict):
            entries = [result]
        if not entries:
            failures.append(f"chunk returned no usable file objects: {result!r}")
            continue

        for item in entries:
            path = str(item.get("path") or chunk[0].get("path") or "unnamed.py")
            content = str(item.get("content") or "")
            language = str(item.get("language") or chunk[0].get("language") or "python")

            if not content.strip():
                failures.append(f"{path}: empty content")
                continue

            entry: dict[str, Any] = {"path": path, "language": language, "content": content}
            generated.append(entry)

            # Write to disk when a workdir is configured (safe relative join).
            if workdir is not None:
                target = (workdir / path.lstrip("/")).resolve()
                if workdir.resolve() in target.parents or target.parent == workdir.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    logger.info("wrote %s (%d bytes)", target, len(content))
                else:
                    failures.append(f"{path}: path escapes workdir, skipped")

    return {
        "phase": "implementation",
        "status": "running",
        "artifacts": {
            "code": {
                "files": generated,
                "chunk_size": chunk_size,
                "failures": failures,
            }
        },
        "messages": [record_message(f"Coder generated {len(generated)} file(s)")],
        "agent_outputs": [
            record_output(
                "agent_coder",
                "code",
                files=len(generated),
                failures=len(failures),
            )
        ],
        "steps_completed": 1,
    }
