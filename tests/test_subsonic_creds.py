"""Persistent Subsonic credentials — token-auth + state.toml round-trip + CLI flags."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from musickit.cli import app
from musickit.tui import state as state_mod
from musickit.tui.subsonic_client import SubsonicClient

# ---------------------------------------------------------------------------
# SubsonicClient — token / salt construction
# ---------------------------------------------------------------------------


def test_client_requires_password_or_token_pair() -> None:
    """Exactly one of `password` or `(token, salt)` must be supplied."""
    with pytest.raises(ValueError, match="requires either"):
        SubsonicClient("http://x", "u")
    # Mixing both is also rejected.
    with pytest.raises(ValueError, match="not both"):
        SubsonicClient("http://x", "u", password="p", token="t", salt="s")


def test_derive_token_round_trips() -> None:
    """`derive_token(p)` returns md5(p + salt) — matches the Subsonic spec."""
    import hashlib

    token, salt = SubsonicClient.derive_token("secret")
    assert len(salt) == 16  # 8 bytes hex
    assert token == hashlib.md5(("secret" + salt).encode("utf-8"), usedforsecurity=False).hexdigest()


def test_auth_params_uses_token_when_token_mode() -> None:
    """Token-mode client sends `t`+`s`, NEVER `p`."""
    cl = SubsonicClient("http://x", "u", token="abc", salt="def")
    params = cl._auth_params()
    assert params["t"] == "abc"
    assert params["s"] == "def"
    assert "p" not in params


def test_auth_params_uses_password_when_password_mode() -> None:
    cl = SubsonicClient("http://x", "u", password="secret")
    params = cl._auth_params()
    assert params["p"] == "secret"
    assert "t" not in params and "s" not in params


def test_auth_for_state_only_works_in_token_mode() -> None:
    """`auth_for_state()` returns saveable dict only when token-mode."""
    pw = SubsonicClient("http://x", "u", password="p")
    assert pw.auth_for_state() is None

    tk = SubsonicClient("http://x", "u", token="abc", salt="def")
    saved = tk.auth_for_state()
    assert saved == {"host": "http://x", "user": "u", "token": "abc", "salt": "def"}


# ---------------------------------------------------------------------------
# state.py — load / save / clear subsonic block
# ---------------------------------------------------------------------------


def test_save_load_subsonic_round_trips(tmp_path: Path) -> None:
    """`save_subsonic` then `load_subsonic` returns the same dict."""
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_subsonic(host="http://h", user="u", token="t", salt="s")
        assert state_mod.load_subsonic() == {
            "host": "http://h",
            "user": "u",
            "token": "t",
            "salt": "s",
        }


def test_load_subsonic_returns_none_when_block_absent(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        assert state_mod.load_subsonic() is None


def test_load_subsonic_returns_none_when_block_partial(tmp_path: Path) -> None:
    """Missing one of the four required fields -> None (don't crash on lookup)."""
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        # Fake a partial subsonic block by writing the state directly.
        state_mod.save_state({"subsonic": {"host": "http://h", "user": "u"}})
        assert state_mod.load_subsonic() is None


def test_clear_subsonic_returns_false_when_nothing_to_clear(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        assert state_mod.clear_subsonic() is False


def test_clear_subsonic_removes_block(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_subsonic(host="http://h", user="u", token="t", salt="s")
        # Other state survives the clear.
        state = state_mod.load_state()
        state["theme"] = "claude-dark"
        state_mod.save_state(state)

        assert state_mod.clear_subsonic() is True
        assert state_mod.load_subsonic() is None
        assert state_mod.load_state().get("theme") == "claude-dark"


def test_save_state_chmods_to_user_only(tmp_path: Path) -> None:
    """Saved state is mode 0600 — the file holds Subsonic auth tokens."""
    import stat

    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_subsonic(host="http://h", user="u", token="t", salt="s")
        mode = stat.S_IMODE(fake.stat().st_mode)
        # Owner read+write, no group / world bits.
        assert mode == 0o600


# ---------------------------------------------------------------------------
# CLI — `--saved-subsonic`, `--save-subsonic`, `--forget-subsonic`
# ---------------------------------------------------------------------------


def test_cli_forget_subsonic_when_nothing_saved(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    runner = CliRunner()
    with patch.object(state_mod, "state_path", return_value=fake):
        result = runner.invoke(app, ["tui", "--forget-subsonic"])
        assert result.exit_code == 0, result.output
        assert "no saved" in result.output.lower()


def test_cli_forget_subsonic_clears_block(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        state_mod.save_subsonic(host="http://h", user="u", token="t", salt="s")
        runner = CliRunner()
        result = runner.invoke(app, ["tui", "--forget-subsonic"])
        assert result.exit_code == 0, result.output
        assert "forgot" in result.output.lower()
        assert state_mod.load_subsonic() is None


def test_cli_saved_subsonic_with_no_block_exits_1(tmp_path: Path) -> None:
    fake = tmp_path / "state.toml"
    with patch.object(state_mod, "state_path", return_value=fake):
        runner = CliRunner()
        result = runner.invoke(app, ["tui", "--saved-subsonic"])
        assert result.exit_code == 1
        assert "no saved credentials" in result.output.lower()


def test_cli_subsonic_without_user_password_exits_1(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["tui", "--subsonic", "http://x"])
    assert result.exit_code == 1
    assert "requires --user and --password" in result.output


# Silence pytest unused-import noise.
_ = pytest
