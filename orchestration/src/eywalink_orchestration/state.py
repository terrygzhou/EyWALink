"""EyWALink orchestration — agent state machine core.

Implements the shared PipelineState TypedDict with explicit reducers.
Reducers control merge semantics so accumulated data (feedback, files,
messages) survives across rework cycles instead of being overwritten by
LangGraph's default shallow merge.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from typing_extensions import NotRequired


def last_value(_a: Any, b: Any) -> Any:
    """Reducer: replace the old value with the new one (default overwrite)."""
    return b


def append_feedback(existing: str, new: str) -> str:
    """Reducer: accumulate human feedback across rework cycles."""
    if not existing:
        return new or ""
    if not new:
        return existing
    return f"{existing}\n---\n{new}"


def append_dict(existing: dict | None, new: dict | None) -> dict:
    """Reducer: shallow-merge nested dicts (e.g. code_files)."""
    existing = dict(existing or {})
    existing.update(new or {})
    return existing


class PipelineState(TypedDict):
    """Shared state for the agent orchestration pipeline.

    All mutable fields use explicit reducers; single-writer fields use
    last_value so the producing node owns the field.
    """

    # --- inputs ---
    project_name: str
    objective: str
    mode: NotRequired[str]  # "auto" (no HIL pauses) | "interactive"

    # --- artifacts (single-writer, overwrite semantics) ---
    requirements_doc: Annotated[str, last_value]
    architecture_doc: Annotated[str, last_value]
    code_files: Annotated[dict[str, str], append_dict]
    qa_report: Annotated[str, last_value]

    # --- gates / routing ---
    req_approved: Annotated[bool, last_value]
    qa_passed: Annotated[bool, last_value]
    rework_count: Annotated[int, last_value]

    # --- accumulated human feedback (append semantics) ---
    human_feedback: Annotated[str, append_feedback]

    # --- runtime / diagnostics ---
    stage_errors: NotRequired[list[str]]
