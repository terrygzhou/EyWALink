"""State persistence: save/load ``pipeline-status.json``.

The status file is the durable record of a pipeline run. It stores the
accumulated state so a run can be resumed after a crash or a container
restart — a core zero-lock-in property: no proprietary checkpoint format.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .state import PipelineState


def save_state(state: PipelineState, path: str | Path) -> Path:
    """Atomically write state to ``path`` as JSON.

    Writes to a temp file in the same directory then renames, so a crash
    mid-write never corrupts the previous checkpoint.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(_serializable(state), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, p)
    return p


def load_state(path: str | Path) -> PipelineState | None:
    """Load state from ``path``. Returns ``None`` if the file is missing."""
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"State file {p} does not contain a JSON object")
    return PipelineState(**data)


def _serializable(state: PipelineState) -> dict[str, Any]:
    """Coerce state to JSON-serializable primitives."""
    return json.loads(json.dumps(dict(state), default=str))
