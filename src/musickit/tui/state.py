"""Persistent UI state (theme, AirPlay device) — `~/.config/musickit/state.toml`."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import tomli_w


def state_path() -> Path:
    """Return the persistent UI state path (`state.toml` next to `radio.toml`)."""
    return Path.home() / ".config" / "musickit" / "state.toml"


def _legacy_json_path() -> Path:
    """Pre-TOML location. Auto-migrated on first read, then deleted."""
    return Path.home() / ".config" / "musickit" / "state.json"


def load_state() -> dict[str, Any]:
    """Read the persisted UI state. Returns `{}` if the file is missing or invalid.

    On first run after the JSON→TOML switch, any existing `state.json` is
    migrated to `state.toml` and the legacy file is removed.
    """
    p = state_path()
    if not p.exists():
        legacy = _legacy_json_path()
        if legacy.exists():
            try:
                with legacy.open() as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
            if not isinstance(data, dict):
                return {}
            # Drop the (now unsupported) subsonic block during migration.
            data.pop("subsonic", None)
            save_state(data)
            try:
                legacy.unlink()
            except OSError:  # pragma: no cover — best-effort
                pass
            return data
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    """Write the persisted UI state. Best-effort — silently ignores I/O errors."""
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as f:
            tomli_w.dump(state, f)
    except OSError:  # pragma: no cover — read-only home etc.
        pass
