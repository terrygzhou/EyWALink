"""High-level pipeline API — the stable contract surface.

Thin facade over the graph + persistence layers implementing the four
operations documented in ``docs/api-contracts.md``:

- ``run_pipeline``  — execute (or start) a pipeline for a goal
- ``get_status``    — read the durable run record (status/phase/progress)
- ``resume``        — continue a paused run (HITL approve / request changes)
- ``get_artifacts`` — read the artifacts of a completed run

Everything is plain JSON-serializable data. No LangGraph types leak out of
this module, so callers (CLI, HTTP API, Paperclip integrations) depend only
on the contract — zero lock-in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentContext
from .config import load_pipeline_config
from .graph import build_pipeline_graph
from .hitl import build_reviewed_pipeline_graph, review_resume_approve, review_resume_changes
from .persistence import load_state, save_state
from .state import PipelineState, make_initial_state

try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
except ImportError:  # pragma: no cover - optional dep
    AsyncSqliteSaver = None  # type: ignore[assignment]

DEFAULT_STATUS_FILE = "pipeline-status.json"


def _context(
    config: dict[str, Any],
    workdir: str | Path | None,
    transport: Any | None = None,
) -> AgentContext:
    return AgentContext.from_config(config, workdir=workdir, transport=transport)


def _thread(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _checkpoint_path(status_file: str | Path) -> Path:
    """Derive a durable checkpoint DB path from the status file path.

    ``pipeline-status.json`` -> ``pipeline-status.checkpoint.sqlite``.
    """
    p = Path(status_file)
    return p.with_name(p.stem + ".checkpoint" + p.suffix.replace("json", "sqlite"))


def _checkpointer(status_file: str | Path):
    """Open a SQLite-backed checkpointer tied to ``status_file``.

    Durable across API calls and processes: resuming a paused run only needs
    the same ``status_file``/``thread_id``. Zero lock-in — plain SQLite file.

    ``AsyncSqliteSaver.from_conn_string`` is itself an async context manager,
    so callers use ``async with _checkpointer(path) as saver:``.
    """
    if AsyncSqliteSaver is None:  # pragma: no cover - optional dep
        raise RuntimeError("langgraph-checkpoint-sqlite is not installed")
    path = _checkpoint_path(status_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(path))


async def run_pipeline(
    goal: str,
    config: dict[str, Any] | str | Path,
    *,
    workdir: str | Path | None = None,
    status_file: str | Path = DEFAULT_STATUS_FILE,
    thread_id: str = "run-1",
    review: bool = True,
    auto_approve: bool = False,
    transport: Any | None = None,
) -> dict[str, Any]:
    """Run a pipeline for ``goal`` and persist the durable state.

    - ``config`` may be a loaded dict or a path to a pipeline YAML.
    - With ``review=True`` (default) the graph pauses at the human review
      gate between QA and done; ``auto_approve=True`` resumes immediately.
    - ``transport`` is an optional httpx transport override for tests.
    - Returns the final (or paused) state as a plain dict and writes it to
      ``status_file``. The LangGraph checkpoint for ``thread_id`` is stored
      in a SQLite file next to it so ``resume()`` works across calls.
    """
    cfg = load_pipeline_config(config) if isinstance(config, (str, Path)) else config
    ctx = _context(cfg, workdir=workdir, transport=transport)
    thread = _thread(thread_id)
    state = make_initial_state(goal, pipeline_name=cfg["pipeline"]["name"])

    async with _checkpointer(status_file) as saver:
        graph = (
            build_reviewed_pipeline_graph(ctx, checkpointer=saver)
            if review
            else build_pipeline_graph(ctx)
        )
        result = await graph.ainvoke(state, config=thread)

        if review and "__interrupt__" in result and auto_approve:
            result = await graph.ainvoke(review_resume_approve(), config=thread)

    save_state(result, status_file)
    return dict(result)


def get_status(
    status_file: str | Path = DEFAULT_STATUS_FILE,
) -> dict[str, Any]:
    """Return the durable run record (or ``None`` if no run exists yet).

    Shape: ``{status, phase, goal, steps_completed, reviews_used, error,
    run_id, updated_at}``.
    """
    state = load_state(status_file)
    if state is None:
        return {"status": "not_found", "phase": None, "goal": None}
    return {
        "status": state.get("status", "unknown"),
        "phase": state.get("phase"),
        "goal": state.get("goal"),
        "steps_completed": state.get("steps_completed", 0),
        "reviews_used": state.get("reviews_used", 0),
        "error": state.get("error"),
        "run_id": state.get("run_id"),
        "updated_at": state.get("updated_at"),
    }


def get_artifacts(
    status_file: str | Path = DEFAULT_STATUS_FILE,
) -> dict[str, Any] | None:
    """Return the artifacts of the persisted run (or ``None``)."""
    state = load_state(status_file)
    return None if state is None else dict(state.get("artifacts", {}))


async def resume(
    decision: str,
    *,
    feedback: str | None = None,
    config: dict[str, Any] | str | Path | None = None,
    workdir: str | Path | None = None,
    status_file: str | Path = DEFAULT_STATUS_FILE,
    thread_id: str = "run-1",
    transport: Any | None = None,
) -> dict[str, Any]:
    """Resume a paused run at the human review gate.

    ``decision`` is ``"approve"`` or ``"request_changes"`` (the latter
    requires ``feedback``). Rebuilds the graph (stateless nodes) so only the
    thread_id matters for the LangGraph checkpointer. Persists the final
    state before returning it.
    """
    if decision == "approve":
        command = review_resume_approve()
    elif decision == "request_changes":
        if not feedback:
            raise ValueError("request_changes requires feedback")
        command = review_resume_changes(feedback)
    else:
        raise ValueError(f"unknown resume decision: {decision!r}")

    # Need the graph to resume; config is required because an "approve"
    # resume re-runs QA (the node after the review gate), which needs the
    # LLM client bound to the context.
    cfg: dict[str, Any]
    if config is None:
        raise ValueError("resume() requires config (dict or pipeline YAML path)")
    cfg = load_pipeline_config(config) if isinstance(config, (str, Path)) else config

    ctx = _context(cfg, workdir=workdir, transport=transport)
    async with _checkpointer(status_file) as saver:
        graph = build_reviewed_pipeline_graph(ctx, checkpointer=saver)
        result = await graph.ainvoke(command, config=_thread(thread_id))
    save_state(result, status_file)
    return dict(result)
