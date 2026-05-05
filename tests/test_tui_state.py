"""TUI persistent state — `~/.config/musickit/state.toml` round-trip + JSON migration.

The state file holds the user's last-chosen theme + saved AirPlay device.
It used to be JSON; the auto-migration on first read after the format
switch must work and is otherwise hard to reach. These tests redirect
`state_path()` to a temp dir so we don't touch the user's real config.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from musickit.tui import state as state_mod


def test_load_state_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """No state.toml and no legacy state.json → `{}`."""
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        assert state_mod.load_state() == {}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    """Whatever we save round-trips through TOML on read."""
    fake = tmp_path / "state.toml"
    payload = {
        "theme": "claude-dark",
        "airplay": {"identifier": "AA:BB:CC:DD:EE:FF", "name": "Living Room"},
    }
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_state(payload)
        assert fake.exists()
        loaded = state_mod.load_state()
    assert loaded == payload


def test_load_state_returns_empty_on_invalid_toml(tmp_path: Path) -> None:
    """A garbled TOML file must not crash — silently treat as empty."""
    fake = tmp_path / "state.toml"
    fake.write_text("this is = not [valid toml ===\n", encoding="utf-8")
    with patch.object(state_mod, "state_path", return_value=fake):
        assert state_mod.load_state() == {}


def test_load_state_migrates_legacy_json_and_drops_subsonic(tmp_path: Path) -> None:
    """First run after the JSON→TOML switch: read state.json, rewrite as TOML, delete the legacy file."""
    fake_toml = tmp_path / "state.toml"
    fake_json = tmp_path / "state.json"
    legacy_payload = {
        "theme": "dark",
        "airplay": {"identifier": "11:22:33:44:55:66"},
        # The migration explicitly drops `subsonic` (no longer supported as
        # persistent state).
        "subsonic": {"host": "old", "user": "stale"},
    }
    fake_json.write_text(json.dumps(legacy_payload), encoding="utf-8")

    with (
        patch.object(state_mod, "state_path", return_value=fake_toml),
        patch.object(state_mod, "_legacy_json_path", return_value=fake_json),
    ):
        loaded = state_mod.load_state()

    assert "subsonic" not in loaded, "subsonic block should be dropped during migration"
    assert loaded["theme"] == "dark"
    assert loaded["airplay"] == {"identifier": "11:22:33:44:55:66"}
    assert fake_toml.exists(), "TOML should have been written"
    assert not fake_json.exists(), "legacy JSON should have been removed after migration"


def test_load_state_handles_corrupt_legacy_json(tmp_path: Path) -> None:
    """Corrupt state.json → return empty, do NOT crash and do NOT touch the file."""
    fake_toml = tmp_path / "state.toml"
    fake_json = tmp_path / "state.json"
    fake_json.write_text("{not valid json", encoding="utf-8")

    with (
        patch.object(state_mod, "state_path", return_value=fake_toml),
        patch.object(state_mod, "_legacy_json_path", return_value=fake_json),
    ):
        loaded = state_mod.load_state()

    assert loaded == {}
    assert not fake_toml.exists(), "no TOML written when legacy JSON is corrupt"
    # Legacy file is left in place — we did not consume it.
    assert fake_json.exists()


def test_save_state_creates_parent_dir(tmp_path: Path) -> None:
    """save_state should mkdir-p the config dir if it doesn't exist yet."""
    fake = tmp_path / "deep" / "nested" / "config" / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_state({"theme": "claude-dark"})
        assert fake.exists()
        assert state_mod.load_state() == {"theme": "claude-dark"}
