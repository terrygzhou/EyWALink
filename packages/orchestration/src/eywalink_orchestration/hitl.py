"""Human-in-the-loop review workflow.

Extends the sequential agent pipeline (:mod:`eywalink_orchestration.graph`)
with a **human review gate between QA and done** (per docs/architecture.md
§5): after the QA agent validates the generated code, the graph pauses and
hands control to a human with a summary of what was produced. The human can:

- ``approve`` — mark the run ``done`` (default onward flow).
- ``request_changes`` — send feedback back to the Coder for another pass
  (bounded by ``max_reviews`` to prevent infinite loops).

Mechanics (LangGraph interrupt/resume):

- The review node calls :func:`langgraph.types.interrupt` with a payload of
  the produced artifacts + QA report. Execution suspends; the caller (a CLI,
  the gateway API, or a human operator) reads the payload from the checkpoint.
- The caller resumes with ``Command(resume={"decision": ..., "feedback":
  ...})``. ``interrupt`` returns that value and the node decides routing.

Status semantics: while the run is paused at the gate the durable status is
``running`` — only approval flips it to ``done``. QA's validation outcome
lives in ``artifacts.qa_report.passed``; a failed validation routes to ``END``
with ``status=blocked`` before the gate is ever reached.

Persistence: the graph must be compiled with a checkpointer (an
``InMemorySaver`` is the default here; the API facade uses a SQLite-backed
saver) so the paused state survives across processes — this is the
zero-lock-in equivalent of a proprietary approval queue: the checkpoint is a
plain, resumable state.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import AgentContext, agent_architect, agent_coder, agent_pm, agent_qa, make_node
from .state import PipelineState

#: Position after which the human review gate is inserted: between QA and done.
DEFAULT_REVIEW_AFTER = "agent_qa"

NODES: tuple[str, ...] = ("agent_pm", "agent_architect", "agent_coder", "agent_qa")

#: Resume payload keys the review node understands.
DECISION_APPROVE = "approve"
DECISION_CHANGES = "request_changes"


def _review_payload(state: PipelineState, review_after: str) -> dict[str, Any]:
    """Summarise the artifacts the human is being asked to review."""
    artifacts = state.get("artifacts") or {}
    qa_report = artifacts.get("qa_report") or {}
    payload: dict[str, Any] = {
        "type": "human_review",
        "phase": state.get("phase", "unknown"),
        "review_after": review_after,
        "summary": {
            "spec": artifacts.get("spec"),
            "architecture": artifacts.get("architecture"),
            "code_files": [
                f.get("path") for f in (artifacts.get("code") or {}).get("files") or []
            ],
            "qa_report": {
                "passed": qa_report.get("passed"),
                "files_validated": qa_report.get("files_validated"),
            },
        },
    }
    return payload


def _review_node(
    state: PipelineState, ctx: AgentContext, review_after: str
) -> dict[str, Any]:
    """Pause for human approval; resume with a decision."""
    # interrupt() suspends the graph and returns the resumed value.
    decision: dict[str, Any] = interrupt(_review_payload(state, review_after))

    reviews_used = state.get("reviews_used", 0)
    if decision.get("decision") == DECISION_APPROVE:
        # The gate sits between QA and done: approval IS the terminal step.
        if _next(review_after) == END:
            return {
                "phase": "done",
                "status": "done",
                "review_feedback": None,
                "messages": [
                    {"role": "system", "content": "Human approved the produced work."}
                ],
                "reviews_used": 1,  # reducer: operator.add
            }
        # Non-terminal gate (custom review_after): stay running, continue chain.
        return {
            "phase": "review",
            "status": "running",
            "messages": [
                {"role": "system", "content": "Human approved the produced work."}
            ],
            "reviews_used": 1,  # reducer: operator.add
        }

    # request_changes: route back to the Coder with feedback.
    feedback = str(decision.get("feedback") or "Please revise the generated code.")
    if reviews_used >= ctx.max_steps:
        return {
            "status": "failed",
            "error": f"human review exceeded max_steps={ctx.max_steps}",
        }
    return {
        "phase": "implementation",
        "status": "running",
        "review_feedback": feedback,
        "reviews_used": 1,  # reducer: operator.add
        "messages": [
            {"role": "system", "content": f"Human requested changes: {feedback}"}
        ],
    }


def _route_after_review(state: PipelineState) -> str:
    """Continue to done on approval; loop back to Coder on requested changes."""
    if state.get("review_feedback"):
        return "redo"
    return "continue"


def _route_failure(state: PipelineState) -> str:
    if state.get("status") in ("failed", "blocked"):
        return "end"
    return "continue"


def _next(node: str) -> str:
    idx = NODES.index(node)
    return NODES[idx + 1] if idx + 1 < len(NODES) else END


async def _coder_with_feedback(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Wrap ``agent_coder`` so human feedback reaches the next generation.

    The base coder reads ``state[\"goal\"]``; when a review requested changes,
    we append the feedback so the next pass addresses it, then delegate.
    """
    feedback = state.get("review_feedback")
    if feedback:
        augmented = dict(state)
        augmented["goal"] = (
            f"{state.get('goal', '')}\n\nHuman review feedback to address: {feedback}"
        )
        return await agent_coder(augmented, ctx)  # type: ignore[arg-type]
    return await agent_coder(state, ctx)


async def _qa_for_review(state: PipelineState, ctx: AgentContext) -> dict[str, Any]:
    """Wrap ``agent_qa`` so a validated run is NOT marked done yet.

    In a reviewed pipeline the run is only ``done`` after human approval, so
    QA reports ``running`` (validation outcome stays in ``qa_report.passed``).
    A failed validation still reports ``blocked`` so the graph stops.
    """
    partial = await agent_qa(state, ctx)
    partial = dict(partial)
    report = (partial.get("artifacts") or {}).get("qa_report") or {}
    partial["status"] = "blocked" if report.get("passed") is False else "running"
    return partial


def build_reviewed_pipeline_graph(
    ctx: AgentContext,
    *,
    review_after: str = DEFAULT_REVIEW_AFTER,
    checkpointer: Any | None = None,
) -> Any:
    """Build the sequential pipeline with a human review gate after QA.

    Args:
        ctx: Runtime context (LLM client, config, workdir).
        review_after: Node after which the review gate is inserted. Must be
            one of the pipeline nodes.
        checkpointer: LangGraph checkpointer for interrupt persistence.
            Defaults to ``InMemorySaver`` (process-local; the API facade
            passes a durable SQLite-backed saver).

    Returns:
        A compiled ``CompiledStateGraph``. Invoke with a thread config
        (``{"configurable": {"thread_id": ...}}``) so the pause/resume
        state is addressable.
    """
    if review_after not in NODES:
        raise ValueError(f"review_after must be one of {NODES}, got {review_after!r}")

    graph: Any = StateGraph(PipelineState)
    graph.add_node("agent_pm", make_node("agent_pm", agent_pm, ctx))
    graph.add_node("agent_architect", make_node("agent_architect", agent_architect, ctx))
    graph.add_node("agent_coder", make_node("agent_coder", _coder_with_feedback, ctx))
    # In the reviewed pipeline QA never reports done itself; the gate decides.
    graph.add_node("agent_qa", make_node("agent_qa", _qa_for_review, ctx))

    async def review_node(state: PipelineState) -> dict[str, Any]:
        return _review_node(state, ctx, review_after)

    graph.add_node("human_review", review_node)

    graph.add_edge(START, "agent_pm")
    # Chain nodes up to the review gate.
    for node in NODES:
        if node == review_after:
            graph.add_conditional_edges(
                node, _route_failure, {"continue": "human_review", "end": END}
            )
            break
        graph.add_conditional_edges(
            node, _route_failure, {"continue": _next(node), "end": END}
        )

    # Review gate routing: approve -> next node (END when after QA),
    # changes -> back to Coder.
    graph.add_conditional_edges(
        "human_review",
        _route_after_review,
        {"redo": "agent_coder", "continue": _next(review_after)},
    )
    # Tail after the review gate: keep failing->END semantics for QA.
    tail = _next(review_after)
    if tail != END:
        graph.add_conditional_edges(
            tail, _route_failure, {"continue": END, "end": END}
        )

    saver = checkpointer or InMemorySaver()
    return graph.compile(checkpointer=saver)


def review_resume_approve() -> Command:
    """Resume command approving the produced work."""
    return Command(resume={"decision": DECISION_APPROVE})


def review_resume_changes(feedback: str) -> Command:
    """Resume command requesting changes with feedback."""
    return Command(resume={"decision": DECISION_CHANGES, "feedback": feedback})


__all__ = [
    "DECISION_APPROVE",
    "DECISION_CHANGES",
    "DEFAULT_REVIEW_AFTER",
    "build_reviewed_pipeline_graph",
    "review_resume_approve",
    "review_resume_changes",
]
