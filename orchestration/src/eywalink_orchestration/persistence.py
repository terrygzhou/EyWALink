"""State persistence — save/load pipeline-status.json for session resume.

A new agent session can pick up from the latest persisted state instead of
re-doing completed work. Persist artifacts as plain JSON with a schema
version for forward compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class StatePersistenceError(RuntimeError):
    pass


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    """Persist state to a JSON file (creates parent dirs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "state": state,
    }
    with p.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def load_state(path: str | Path) -> dict[str, Any] | None:
    """Load persisted state. Returns None if missing or malformed.

    Never raises on a corrupt file — the caller can restart fresh.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        state = payload.get("state")
        return state if isinstance(state, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def state_file_path(state_dir: str | Path, pipeline_name: str) -> Path:
    """Resolve the status file path under a state directory."""
    return Path(state_dir) / f"{pipeline_name}-status.json"
