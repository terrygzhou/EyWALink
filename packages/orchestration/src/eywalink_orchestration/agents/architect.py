"""agent_architect — technical design and architecture doc.

Consumes the PM spec and produces a technical design
(``artifacts["architecture"]``) including the file plan that the Coder node
uses to generate code chunk-by-chunk.

The file plan is the key contract: the Coder generates one file at a time
from it, so the Architect must enumerate every file to be created.
"""

from __future__ import annotations

from typing import Any

from ..llm import LLMError
from ..state import PipelineState
from .base import AgentContext, fail, record_message, record_output

ARCH_KEYS = ("overview", "components", "data_flow", "tech_stack", "file_plan", "risks")

SYSTEM_PROMPT = """You are the Architect agent in an autonomous software pipeline.
Given the requirements specification, produce a technical design.

Respond with a JSON object ONLY, using exactly this shape:
{
  "overview": "2-4 sentence architecture overview",
  "components": [
    {"name": "component", "responsibility": "what it does", "tech": "tech used"}
  ],
  "data_flow": ["step 1", "step 2"],
  "tech_stack": {"language": "python", "framework": "fastapi", "storage": "postgres"},
  "file_plan": [
    {"path": "src/app.py", "purpose": "what this file does", "language": "python"}
  ],
  "risks": ["risk 1"]
}
The file_plan MUST enumerate every file the Coder should create, one entry per
file, with a relative path. Keep the design minimal — prefer fewer files and
simple components. Do not write code."""


async def agent_architect(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Technical design + architecture doc node."""
    spec = (state.get("artifacts") or {}).get("spec") or {}
    goal = state.get("goal", "")

    user_prompt = f"User goal:\n{goal}\n\nRequirements specification:\n{spec}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        arch = await ctx.llm.complete_json(messages, max_tokens=3072)
    except LLMError as exc:
        return fail(state, "agent_architect", exc)

    clean: dict[str, Any] = {k: arch.get(k) for k in ARCH_KEYS if k in arch}
    if "file_plan" not in clean or not isinstance(clean["file_plan"], list):
        clean["file_plan"] = []

    return {
        "phase": "architecture",
        "status": "running",
        "artifacts": {"architecture": clean},
        "messages": [record_message(f"Architecture designed: {clean.get('overview', '')[:80]}")],
        "agent_outputs": [
            record_output(
                "agent_architect",
                "architecture",
                components=len(clean.get("components", [])),
                files_planned=len(clean.get("file_plan", [])),
            )
        ],
        "steps_completed": 1,
    }
