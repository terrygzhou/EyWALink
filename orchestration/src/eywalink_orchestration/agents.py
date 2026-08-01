"""Agent nodes: PM, Architect, Coder, QA.

Each agent is a node function `(state) -> partial state update` bound to a
shared runtime context (LLM client, MCP registry, output dir). Nodes run
SEQUENTIALLY — never in parallel — because local LLMs share VRAM and
concurrent requests degrade throughput and cause timeouts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .llm import LLMClient
from .mcp import MCPRegistry
from .state import PipelineState

logger = logging.getLogger(__name__)

MAX_TOKENS_QUICK = 2048
MAX_TOKENS_DOC = 4096
MAX_TOKENS_CODE = 4096


@dataclass
class NodeContext:
    """Shared runtime context injected into every agent node."""

    llm: LLMClient
    config: dict[str, Any] = field(default_factory=dict)
    mcp: MCPRegistry | None = None
    output_dir: Path | None = None


# ---------------------------------------------------------------------- #
# Prompt templates
# ---------------------------------------------------------------------- #
PM_SYSTEM = (
    "You are a senior product manager. Produce a crisp, actionable "
    "requirements document. Be specific and structured."
)

ARCHITECT_SYSTEM = (
    "You are a principal software architect. Produce a technical design "
    "with components, data flow, and API surface. Prefer simple, "
    "maintainable, zero-lock-in choices."
)

CODER_SYSTEM = (
    "You are a senior engineer. Write clean, working, minimal code. "
    "No placeholders, no explanations outside code comments."
)

QA_SYSTEM = (
    "You are a QA engineer. Review the generated code for correctness, "
    "then reply with a verdict line starting with APPROVED or REJECTED, "
    "followed by concrete findings."
)


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


# ---------------------------------------------------------------------- #
# PM — requirements gathering and spec generation
# ---------------------------------------------------------------------- #
def make_agent_pm(ctx: NodeContext) -> Any:
    def agent_pm(state: PipelineState) -> dict:
        prompt = (
            f"Project: {state.get('project_name', '')}\n"
            f"Objective: {state.get('objective', '')}\n\n"
            "Write the requirements document (scope, functional "
            "requirements, non-functional requirements, acceptance criteria)."
        )
        doc = ctx.llm.chat_text(prompt, system=PM_SYSTEM, max_tokens=MAX_TOKENS_DOC)
        logger.info("[PM] requirements_doc generated (%d chars)", len(doc))
        return {"requirements_doc": doc}

    return agent_pm


# ---------------------------------------------------------------------- #
# Architect — technical design
# ---------------------------------------------------------------------- #
def make_agent_architect(ctx: NodeContext) -> Any:
    def agent_architect(state: PipelineState) -> dict:
        prompt = (
            "Requirements:\n"
            f"{_truncate(state.get('requirements_doc', ''))}\n\n"
            "Write the technical design: system components, data flow, "
            "module layout, and API contracts. Prefer self-hosted, "
            "open-source, zero-lock-in choices."
        )
        doc = ctx.llm.chat_text(prompt, system=ARCHITECT_SYSTEM, max_tokens=MAX_TOKENS_DOC)
        logger.info("[Architect] architecture_doc generated (%d chars)", len(doc))
        return {"architecture_doc": doc}

    return agent_architect


# ---------------------------------------------------------------------- #
# Coder — chunked, file-by-file code generation (never one giant blob)
# ---------------------------------------------------------------------- #
def make_agent_coder(ctx: NodeContext) -> Any:
    def agent_coder(state: PipelineState) -> dict:
        arch = _truncate(state.get("architecture_doc", ""))
        reqs = _truncate(state.get("requirements_doc", ""), 2000)
        prompt_manifest = (
            "Based on the design below, list the files to create as a JSON "
            "object: {\"files\": [\"path/to/file.py\", ...]}. Keep the file "
            "set small (max 5 files) and focused.\n\n"
            f"Design:\n{arch}\n\nRequirements:\n{reqs}"
        )
        manifest = ctx.llm.chat_json(prompt_manifest, system=CODER_SYSTEM, max_tokens=MAX_TOKENS_QUICK)
        files = manifest.get("files", [])
        if not isinstance(files, list) or not files:
            files = ["main.py"]

        code_files: dict[str, str] = {}
        for filepath in files:
            filepath = str(filepath).strip()
            if not filepath:
                continue
            prompt_file = (
                "Generate the complete content for this single file.\n"
                f"File: {filepath}\n\n"
                f"Project: {state.get('project_name', '')}\n"
                f"Design:\n{arch}\n\n"
                "Output ONLY the file content, no markdown fences, no commentary."
            )
            content = ctx.llm.chat_text(prompt_file, system=CODER_SYSTEM, max_tokens=MAX_TOKENS_CODE)
            code_files[filepath] = content
            logger.info("[Coder] generated %s (%d chars)", filepath, len(content))

            # Persist to disk when an output dir is configured (chunked writes).
            if ctx.output_dir is not None:
                target = Path(ctx.output_dir) / filepath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

        return {"code_files": code_files}

    return agent_coder


# ---------------------------------------------------------------------- #
# QA — test generation and validation
# ---------------------------------------------------------------------- #
def make_agent_qa(ctx: NodeContext) -> Any:
    def agent_qa(state: PipelineState) -> dict:
        files_blob = "\n\n".join(
            f"--- {path} ---\n{_truncate(content, 1200)}"
            for path, content in state.get("code_files", {}).items()
        )
        prompt = (
            "Review this code for correctness, bugs, and missing pieces.\n\n"
            f"{files_blob}\n\n"
            "End your review with a verdict line starting with exactly "
            "APPROVED or REJECTED."
        )
        report = ctx.llm.chat_text(prompt, system=QA_SYSTEM, max_tokens=MAX_TOKENS_DOC)
        passed = "APPROVED" in report.upper()[:200]
        logger.info("[QA] report generated, verdict=%s", "APPROVED" if passed else "REJECTED")
        return {"qa_report": report, "qa_passed": passed}

    return agent_qa
