"""agent_pm — requirements gathering and spec generation.

The PM agent is the first node in the pipeline. It takes the raw user goal
and produces a structured requirements specification (``artifacts["spec"]``)
that the Architect node consumes.

LLM contract: the model returns a JSON object with the spec shape below.
The prompt is deliberately explicit about the schema so it works on plain
OpenAI-compatible servers (SGLang / vLLM / Ollama) with JSON mode enabled.
"""

from __future__ import annotations

from typing import Any

from ..llm import LLMError
from ..state import PipelineState
from .base import AgentContext, fail, record_message, record_output

SPEC_KEYS = ("title", "summary", "requirements", "acceptance_criteria", "out_of_scope")

SYSTEM_PROMPT = """You are the Product Manager agent in an autonomous software pipeline.
Your job is to turn a user goal into a precise, testable requirements specification.

Respond with a JSON object ONLY, using exactly this shape:
{
  "title": "short feature title",
  "summary": "2-4 sentence summary of what we are building and why",
  "requirements": [
    {"id": "R1", "description": "requirement text", "priority": "must|should|could"}
  ],
  "acceptance_criteria": ["criterion 1", "criterion 2"],
  "out_of_scope": ["explicitly excluded item"]
}
Keep requirements concrete and verifiable. Do not write code. Do not design the
implementation — that is the Architect's job."""


async def agent_pm(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Requirements gathering + spec generation node."""
    goal = state.get("goal", "")
    if not goal.strip():
        return fail(state, "agent_pm", ValueError("state['goal'] is empty"))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"User goal:\n{goal}"},
    ]
    try:
        spec = await ctx.llm.complete_json(messages, max_tokens=2048)
    except LLMError as exc:
        return fail(state, "agent_pm", exc)

    # Normalize: keep only known keys so downstream nodes get a stable shape.
    clean: dict[str, Any] = {k: spec.get(k) for k in SPEC_KEYS if k in spec}
    if "title" not in clean:
        clean["title"] = goal[:80]
    if "requirements" not in clean or not isinstance(clean["requirements"], list):
        clean["requirements"] = []

    return {
        "phase": "requirements",
        "status": "running",
        "artifacts": {"spec": clean},
        "messages": [record_message(f"PM spec generated: {clean.get('title')}")],
        "agent_outputs": [
            record_output(
                "agent_pm",
                "spec",
                title=clean.get("title"),
                requirements=len(clean.get("requirements", [])),
            )
        ],
        "steps_completed": 1,
    }
