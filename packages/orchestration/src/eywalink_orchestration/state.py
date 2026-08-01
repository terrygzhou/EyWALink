"""Pipeline state schema with explicit reducers.

The state is a LangGraph ``TypedDict``. Every multi-value field uses an
explicit reducer (via ``typing.Annotated``) so that sequential agent nodes
merge their partial updates instead of overwriting the accumulated state.

Design notes:
- ``messages`` and ``agent_outputs`` append (``operator.add``).
- ``artifacts`` uses a dict-merge reducer keyed by artifact name so the
  PM, Architect, Coder, and QA agents each write their own section.
- Scalar fields (``goal``, ``phase``, ``status``) are last-write-wins by
  default, which is the correct semantic for sequential single-writer steps.
- Reducers are declared on the TypedDict itself because LangGraph reads
  reducer annotations from the state schema via ``Annotated``.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, NotRequired, TypedDict

Phase = Literal[
    "init",
    "requirements",
    "architecture",
    "implementation",
    "qa",
    "review",
    "done",
]

Status = Literal["pending", "running", "blocked", "done", "failed"]


def merge_artifacts(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge artifact sections keyed by name (right wins per key)."""
    merged: dict[str, Any] = dict(left or {})
    if right:
        merged.update(right)
    return merged


class PipelineState(TypedDict, total=False):
    """Shared state flowing through the agent pipeline graph."""

    # --- static configuration -------------------------------------------
    goal: str
    pipeline_name: str

    # --- runtime bookkeeping ---------------------------------------------
    phase: NotRequired[Phase]
    status: NotRequired[Status]
    error: NotRequired[str | None]
    run_id: NotRequired[str]

    # --- human-in-the-loop review ------------------------------------------
    review_feedback: NotRequired[str | None]  # last requested change
    reviews_used: NotRequired[Annotated[int, operator.add]]

    # --- conversation / agent traces --------------------------------------
    messages: NotRequired[Annotated[list[dict[str, Any]], operator.add]]
    agent_outputs: NotRequired[Annotated[list[dict[str, Any]], operator.add]]

    # --- work products -----------------------------------------------------
    # Reducer: merge_artifacts (dict keyed by artifact name).
    artifacts: NotRequired[Annotated[dict[str, Any], merge_artifacts]]

    # --- metrics -----------------------------------------------------------
    tokens_used: NotRequired[Annotated[int, operator.add]]
    steps_completed: NotRequired[Annotated[int, operator.add]]


# Kept for backwards compatibility; LangGraph reads the Annotated reducers
# declared on the TypedDict above. Nodes should rely on the TypedDict, not
# this dict.
REDUCERS: dict[str, Any] = {
    "messages": operator.add,
    "agent_outputs": operator.add,
    "artifacts": merge_artifacts,
    "tokens_used": operator.add,
    "steps_completed": operator.add,
    "reviews_used": operator.add,
}


def make_initial_state(goal: str, pipeline_name: str = "default") -> PipelineState:
    """Create a fresh pipeline state for a new run."""
    return PipelineState(
        goal=goal,
        pipeline_name=pipeline_name,
        phase="init",
        status="pending",
        error=None,
        messages=[],
        agent_outputs=[],
        artifacts={},
        tokens_used=0,
        steps_completed=0,
    )
