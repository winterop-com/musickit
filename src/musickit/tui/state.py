"""Persistent UI state (theme selection, etc.) — `~/.config/musickit/state.json`."""

from __future__ import annotations

import json
from pathlib import Path


def state_path() -> Path:
    """Persistent UI state (theme selection, etc.) lives in `state.json`."""
    return Path.home() / ".config" / "musickit" / "state.json"


def load_state() -> dict[str, object]:
    """Read the persisted UI state. Returns `{}` if the file is missing or invalid."""
    p = state_path()
    if not p.exists():
        return {}
    try:
        with p.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, object]) -> None:
    """Write the persisted UI state. Best-effort — silently ignores I/O errors."""
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            json.dump(state, f, indent=2)
    except OSError:  # pragma: no cover — read-only home etc.
        pass
