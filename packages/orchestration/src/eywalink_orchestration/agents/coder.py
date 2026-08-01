"""agent_coder — chunked code generation (file-by-file).

The Coder consumes the Architect's file plan and generates code **in chunks**
(chunk size configurable via ``agents.coder.chunk_size``, default 1). It never
asks the LLM for the whole codebase in one request — that keeps requests small
enough for budget-friendly local models and avoids timeouts.

Chunking contract: a chunk of N files is requested together in one LLM call.
The model may answer with a single file object or a list of file objects; any
requested file the model omits is re-requested individually, so a short
response never silently drops a planned file.

Files are written under ``ctx.workdir`` when provided; the generated file
contents are recorded in ``artifacts[\"code\"]`` so the QA agent can validate
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
You implement the requested file(s) following the architecture's file plan.

Respond with a JSON object ONLY. For a single file, use exactly this shape:
{"path": "src/app.py", "language": "python", "content": "the complete source code"}
For multiple files, respond with a JSON array of the same objects, one per file.
Rules:
- Output complete, working code for every requested file.
- Match the file plan path and language exactly.
- Do not explain the code. Do not include markdown fences in "content"."""


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split a list into sequential chunks of at most ``size``."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _as_file_list(result: Any) -> list[dict[str, Any]]:
    """Normalise an LLM chunk response to a list of file objects."""
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


async def _request_files(
    ctx: AgentContext, context_blob: str, files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ask the LLM for a set of files; return normalised file objects."""
    user_prompt = (
        f"Project context:\n{context_blob}\n\n"
        f"Files to implement in THIS request:\n{json.dumps(files, default=str)}\n"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    result = await ctx.llm.complete_json(messages, max_tokens=4096)
    return _as_file_list(result)


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
        requested_paths = [str(f.get("path", "")) for f in chunk]
        try:
            entries = await _request_files(ctx, context_blob, chunk)
        except LLMError as exc:
            failures.append(str(exc))
            logger.warning("coder chunk failed: %s", exc)
            continue

        # Attribute returned entries to requested paths; re-request any file
        # the model skipped so a short response never drops a planned file.
        produced: list[dict[str, Any]] = []
        covered: set[str] = set()
        for entry in entries:
            path = str(entry.get("path") or "")
            if path not in requested_paths:
                path = next((p for p in requested_paths if p not in covered), path or "unnamed.py")
            content = str(entry.get("content") or "")
            language = str(entry.get("language") or "python")
            if not content.strip():
                failures.append(f"{path}: empty content")
                continue
            produced.append({"path": path, "language": language, "content": content})
            covered.add(path)

        for plan_item in chunk:
            path = str(plan_item.get("path", ""))
            if path in covered or not path:
                continue
            try:
                fallback_entries = await _request_files(ctx, context_blob, [plan_item])
            except LLMError as exc:
                failures.append(f"{path}: {exc}")
                continue
            if not fallback_entries:
                failures.append(f"{path}: empty response")
                continue
            entry = fallback_entries[0]
            content = str(entry.get("content") or "")
            if not content.strip():
                failures.append(f"{path}: empty content")
                continue
            produced.append(
                {
                    "path": path,
                    "language": str(plan_item.get("language") or entry.get("language") or "python"),
                    "content": content,
                }
            )
            covered.add(path)

        for entry in produced:
            generated.append(entry)
            # Write to disk when a workdir is configured (safe relative join).
            if workdir is not None:
                target = (workdir / entry["path"].lstrip("/")).resolve()
                if workdir.resolve() in target.parents or target.parent == workdir.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(entry["content"], encoding="utf-8")
                    logger.info("wrote %s (%d bytes)", target, len(entry["content"]))
                else:
                    failures.append(f"{entry['path']}: path escapes workdir, skipped")

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
