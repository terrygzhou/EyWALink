"""Sequential pipeline graph wiring the four core agent nodes.

Executes ``PM -> Architect -> Coder -> QA`` strictly sequentially: a linear
chain with no parallel branches, because the local LLM server (SGLang /
vLLM on a single GPU) has VRAM contention — concurrent requests would
time out or OOM.

Failure routing: if any node returns ``status == "failed"`` (or the QA
validation blocks), the graph routes to ``END`` instead of continuing, so a
broken step never produces downstream work.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .agents import AgentContext, agent_architect, agent_coder, agent_pm, agent_qa, make_node
from .state import PipelineState

NODES: tuple[str, ...] = ("agent_pm", "agent_architect", "agent_coder", "agent_qa")


def _route(state: PipelineState) -> str:
    """Return the next step: continue the chain or stop.

    Stops when a node failed, or when QA validation blocked the run.
    """
    status = state.get("status")
    if status in ("failed", "blocked"):
        return "end"
    return "continue"


def build_pipeline_graph(ctx: AgentContext) -> Any:
    """Compile the sequential agent pipeline graph.

    Args:
        ctx: Runtime context (LLM client, config, workdir) shared by all nodes.

    Returns:
        A compiled ``CompiledStateGraph``. Invoke with
        ``await graph.ainvoke(make_initial_state(goal))``.
    """
    graph: Any = StateGraph(PipelineState)
    graph.add_node("agent_pm", make_node("agent_pm", agent_pm, ctx))
    graph.add_node("agent_architect", make_node("agent_architect", agent_architect, ctx))
    graph.add_node("agent_coder", make_node("agent_coder", agent_coder, ctx))
    graph.add_node("agent_qa", make_node("agent_qa", agent_qa, ctx))

    graph.add_edge(START, "agent_pm")
    for node in NODES:
        graph.add_conditional_edges(node, _route, {"continue": _next(node), "end": END})
    return graph.compile()


def _next(node: str) -> str:
    """Return the next node in the sequential chain (or END)."""
    idx = NODES.index(node)
    return NODES[idx + 1] if idx + 1 < len(NODES) else END


__all__ = ["NODES", "build_pipeline_graph"]
