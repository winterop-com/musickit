"""Persistent UI state (theme, AirPlay device) — `~/.config/musickit/state.toml`."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from musickit import _toml_dump


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
    """Write the persisted UI state. Best-effort — silently ignores I/O errors.

    Writes are mode-0600 so the file containing Subsonic auth tokens
    isn't world-readable on a multi-user box.
    """
    p = state_path()
    try:
        _toml_dump.dump_path(state, p)
        # Tighten perms — best-effort. No-op on Windows / where chmod fails.
        try:
            p.chmod(0o600)
        except OSError:  # pragma: no cover — Windows / read-only mounts
            pass
    except OSError:  # pragma: no cover — read-only home etc.
        pass


# ---------------------------------------------------------------------------
# Subsonic-credential helpers — `<state>.subsonic = {host, user, token, salt}`
# ---------------------------------------------------------------------------


def load_subsonic() -> dict[str, str] | None:
    """Read the saved Subsonic auth block, or None if absent / malformed.

    Validates that all four required fields are present and non-empty;
    a partial / corrupt block returns None so the caller asks for fresh
    credentials instead of crashing on a missing `salt`.
    """
    state = load_state()
    block = state.get("subsonic")
    if not isinstance(block, dict):
        return None
    required = ("host", "user", "token", "salt")
    out: dict[str, str] = {}
    for k in required:
        v = block.get(k)
        if not isinstance(v, str) or not v:
            return None
        out[k] = v
    return out


def save_subsonic(*, host: str, user: str, token: str, salt: str) -> None:
    """Persist a Subsonic auth pair. Overwrites any prior subsonic block."""
    state = load_state()
    state["subsonic"] = {"host": host, "user": user, "token": token, "salt": salt}
    save_state(state)


def clear_subsonic() -> bool:
    """Remove the saved Subsonic block. Returns True iff something was removed."""
    state = load_state()
    if "subsonic" not in state:
        return False
    state.pop("subsonic", None)
    save_state(state)
    return True
