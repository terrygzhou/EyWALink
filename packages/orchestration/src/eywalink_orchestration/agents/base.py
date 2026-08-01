"""Agent node runtime context and shared helpers.

Every agent node has the same contract:

- **state dict**: a ``PipelineState`` (LangGraph TypedDict) carrying the
  accumulated run state (goal, artifacts, messages, metrics).
- **runtime context**: an ``AgentContext`` with the LLM client, pipeline
  config, and optional working directory.
- **returns**: a *partial state update* — a plain dict of keys to merge
  into the accumulated state. Reducers on the TypedDict decide append vs
  overwrite semantics, so nodes never mutate the incoming state.

Nodes are wrapped for LangGraph via :func:`make_node`, which binds the
context and exposes the ``(state) -> partial`` signature LangGraph expects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import DEFAULT_CONFIG
from ..llm import LLMClient
from ..state import PipelineState

logger = logging.getLogger(__name__)

#: Signature of an agent node implementation.
AgentFn = Callable[[PipelineState, "AgentContext"], Awaitable[dict[str, Any]]]


@dataclass
class AgentContext:
    """Runtime context injected into every agent node.

    Attributes:
        llm: Shared :class:`LLMClient` (sequential use only — no parallel
            requests, VRAM contention on local model servers).
        config: Deep-merged pipeline config (see :mod:`config`).
        workdir: Optional directory for file-writing agents (coder, QA).
        max_steps: Safety cap on pipeline steps.
    """

    llm: LLMClient
    config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    workdir: Path | None = None
    max_steps: int = 50

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        workdir: str | Path | None = None,
        *,
        api_key: str = "not-needed",
        transport: Any | None = None,
    ) -> "AgentContext":
        """Build a context from a loaded pipeline config dict.

        ``transport`` is an optional httpx transport override (used by tests
        to inject a mock); ``None`` means the real HTTP transport.
        """
        llm_cfg = config["pipeline"]["llm"]
        llm = LLMClient(
            base_url=llm_cfg["base_url"],
            model=llm_cfg["model"],
            api_key=api_key,
            temperature=llm_cfg.get("temperature", DEFAULT_CONFIG["pipeline"]["llm"]["temperature"]),
            max_tokens=llm_cfg.get("max_tokens", DEFAULT_CONFIG["pipeline"]["llm"]["max_tokens"]),
            transport=transport,
        )
        return cls(
            llm=llm,
            config=config,
            workdir=Path(workdir) if workdir else None,
            max_steps=int(config["pipeline"].get("max_steps", 50)),
        )


def make_node(name: str, fn: AgentFn, ctx: AgentContext) -> Callable[[PipelineState], Awaitable[dict[str, Any]]]:
    """Bind a context to an agent implementation for LangGraph.

    LangGraph node functions take exactly one argument (the state). This
    wrapper closes over the runtime context and returns the callable that
    LangGraph's ``StateGraph.add_node`` expects.
    """

    async def node(state: PipelineState) -> dict[str, Any]:
        return await fn(state, ctx)

    node.__name__ = name
    return node


def record_output(agent: str, artifact: str, **details: Any) -> dict[str, Any]:
    """Build an ``agent_outputs`` trace entry for a node."""
    return {"agent": agent, "artifact": artifact, **details}


def record_message(content: str, *, role: str = "assistant") -> dict[str, str]:
    """Build a ``messages`` trace entry."""
    return {"role": role, "content": content}


def fail(state: PipelineState, agent: str, error: Exception) -> dict[str, Any]:
    """Return a partial update marking the run failed (and log it)."""
    logger.error("agent %s failed: %s", agent, error)
    return {
        "status": "failed",
        "error": f"{agent}: {error}",
        "phase": state.get("phase", "init"),
        "steps_completed": 1,
    }
