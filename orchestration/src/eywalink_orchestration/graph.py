"""LangGraph workflow: PM -> Architect -> Coder -> QA with HIL gate.

Design decisions (see docs/architecture.md):
- StateGraph over the functional API — explicit nodes/edges, auditable.
- Sequential nodes only — no parallel execution (local LLM VRAM contention).
- Human-in-the-loop via LangGraph interrupt() in the gate node; compiled
  with auto_approve=False so the gate actually pauses in interactive mode.
- In "auto" mode (tests/CI) the gate auto-approves and the graph runs
  end-to-end without human input.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import (
    NodeContext,
    make_agent_architect,
    make_agent_coder,
    make_agent_pm,
    make_agent_qa,
)
from .state import PipelineState

logger = logging.getLogger(__name__)

MAX_REWORK = 2


def make_human_gate() -> Any:
    """Human-in-the-loop gate node.

    In interactive mode it calls interrupt() with the pipeline artifacts and
    waits for a human decision. Resume with Command(resume=[{...}]).
    In auto mode it approves immediately (test/CI path).

    Returns Command(goto=...) so it can both update state and route — the
    supported pattern for nodes in langgraph 1.2.x (conditional-edge path
    functions must return plain node names, not Command).
    """

    def human_gate(state: PipelineState) -> Command:
        if state.get("mode") == "auto":
            return Command(goto=END, update={
                "req_approved": True,
                "qa_passed": state.get("qa_passed", True),
            })
        decision = interrupt({
            "question": "Review the pipeline output. Approve and ship, or send back for rework?",
            "project_name": state.get("project_name", ""),
            "requirements_doc": state.get("requirements_doc", "")[:1500],
            "architecture_doc": state.get("architecture_doc", "")[:1500],
            "code_files": list(state.get("code_files", {}).keys()),
            "qa_report": state.get("qa_report", "")[:1500],
        })
        # interrupt() returns the resume payload. LangGraph 1.2.x returns a
        # list wrapping the value passed to Command(resume=[...]); unwrap it.
        if isinstance(decision, list) and decision:
            decision = decision[0]
        decision = decision if isinstance(decision, dict) else {}
        approved = bool(decision.get("approved", False))
        feedback = str(decision.get("feedback", ""))
        logger.info("[HumanGate] approved=%s feedback='%.120s'", approved, feedback)
        if approved:
            return Command(goto=END, update={
                "req_approved": True,
                "qa_passed": True,
                "human_feedback": feedback,
            })
        rework = state.get("rework_count", 0)
        if rework < MAX_REWORK:
            return Command(goto="architect", update={
                "req_approved": False,
                "qa_passed": False,
                "rework_count": rework + 1,
                "human_feedback": feedback,
            })
        return Command(goto=END, update={
            "req_approved": False,
            "qa_passed": False,
            "human_feedback": feedback,
        })

    return human_gate


def route_after_gate(state: PipelineState) -> str:
    """Legacy helper kept for tests/visualization: plain node-name routing.

    NOTE: the compiled graph routes via Command from the human_gate node
    (langgraph 1.2.x pattern). This function documents the same decision
    for graph visualization and is not wired into add_conditional_edges.
    """
    if state.get("req_approved"):
        return END
    rework = state.get("rework_count", 0)
    if rework < MAX_REWORK:
        return "architect"
    return END


def build_graph(ctx: NodeContext) -> Any:
    """Build and compile the orchestration StateGraph."""
    workflow = StateGraph(PipelineState)

    workflow.add_node("pm", make_agent_pm(ctx))
    workflow.add_node("architect", make_agent_architect(ctx))
    workflow.add_node("coder", make_agent_coder(ctx))
    workflow.add_node("qa", make_agent_qa(ctx))
    workflow.add_node("human_gate", make_human_gate())

    workflow.add_edge(START, "pm")
    workflow.add_edge("pm", "architect")
    workflow.add_edge("architect", "coder")
    workflow.add_edge("coder", "qa")
    workflow.add_edge("qa", "human_gate")
    # human_gate returns Command(goto=...) — END or architect (rework) — so
    # no conditional edge wiring is needed on this version of LangGraph.

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def run_pipeline(
    ctx: NodeContext,
    project_name: str,
    objective: str,
    mode: str = "auto",
    thread_id: str = "default",
) -> dict[str, Any]:
    """Convenience runner: invoke the graph end-to-end.

    mode="interactive" pauses at the HIL gate; resume via graph.invoke with
    Command(resume=[{"approved": bool, "feedback": str}]).
    """
    graph = build_graph(ctx)
    initial: PipelineState = {
        "project_name": project_name,
        "objective": objective,
        "mode": mode,
        "requirements_doc": "",
        "architecture_doc": "",
        "code_files": {},
        "qa_report": "",
        "req_approved": False,
        "qa_passed": False,
        "rework_count": 0,
        "human_feedback": "",
    }
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(initial, config=config)
