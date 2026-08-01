"""Core agent nodes: PM, Architect, Coder, QA.

Each node is an async function ``(state, ctx) -> partial-state-update``;
bind with :func:`make_node` for LangGraph. Nodes are executed sequentially
by the pipeline graph (see :mod:`eywalink_orchestration.graph`) — never in
parallel, because the local model server shares a single GPU (VRAM
contention).
"""

from __future__ import annotations

from .architect import agent_architect
from .base import AgentContext, make_node
from .coder import agent_coder
from .pm import agent_pm
from .qa import agent_qa

__all__ = [
    "AgentContext",
    "agent_architect",
    "agent_coder",
    "agent_pm",
    "agent_qa",
    "make_node",
]
