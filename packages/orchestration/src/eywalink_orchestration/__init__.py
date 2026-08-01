"""EyWALink Orchestration Core.

LangGraph-based agent orchestration primitives: pipeline state with explicit
reducers, a resilient LLM client for local model serving, YAML pipeline
configuration, JSON state persistence, the four core agent nodes
(PM, Architect, Coder, QA) wired into a sequential pipeline graph, an MCP
tool integration layer, and a human-in-the-loop review workflow.

Zero lock-in: only depends on open standards (OpenAI-compatible chat API,
MCP, YAML, JSON) and open-source frameworks (LangGraph).
"""

from __future__ import annotations

__version__ = "0.1.0"

from .agents import AgentContext, agent_architect, agent_coder, agent_pm, agent_qa, make_node
from .config import load_pipeline_config
from .graph import NODES, build_pipeline_graph
from .hitl import build_reviewed_pipeline_graph, review_resume_approve, review_resume_changes
from .llm import LLMClient
from .mcp import MCPServerClient, ToolRegistry, mcp_servers_from_config, run_tool_loop
from .persistence import load_state, save_state
from .state import PipelineState, make_initial_state

__all__ = [
    "AgentContext",
    "LLMClient",
    "MCPServerClient",
    "NODES",
    "PipelineState",
    "ToolRegistry",
    "agent_architect",
    "agent_coder",
    "agent_pm",
    "agent_qa",
    "build_pipeline_graph",
    "build_reviewed_pipeline_graph",
    "load_pipeline_config",
    "load_state",
    "make_initial_state",
    "make_node",
    "mcp_servers_from_config",
    "review_resume_approve",
    "review_resume_changes",
    "run_tool_loop",
    "save_state",
]
