"""`musickit config` CLI — show / path / migrate.

Exercises the user-facing surface of the config subcommand. The
underlying logic is tested in `test_config.py`; this file asserts the
CLI wraps it correctly: exit codes, output text, file mutations on
`migrate`. `migrate` is destructive (deletes serve.toml) so tests
operate on a tmp_path-redirected config dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from musickit.cli import app


def _redirect_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point both `config_path()` and `legacy_serve_path()` at tmp_path."""
    from musickit.config import MusickitConfig

    monkeypatch.setattr("musickit.config.config_dir", lambda: tmp_path)
    MusickitConfig.model_config["toml_file"] = str(tmp_path / "musickit.toml")
    # Also wipe MUSICKIT_* env vars so test isolation holds.
    import os

    for key in list(os.environ):
        if key.startswith("MUSICKIT_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# `musickit config path`
# ---------------------------------------------------------------------------


def test_config_path_prints_absolute_path(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`config path` prints the resolved absolute file path and exits 0."""
    _redirect_config(monkeypatch, tmp_path)
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    out = result.stdout.strip()
    assert out == str(tmp_path / "musickit.toml")


# ---------------------------------------------------------------------------
# `musickit config show`
# ---------------------------------------------------------------------------


def test_config_show_with_defaults(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No file + no env → admin/admin defaults rendered, secrets masked."""
    _redirect_config(monkeypatch, tmp_path)
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "username = 'admin'" in result.stdout
    # Default password 'admin' is sensitive even when default-valued, mask it.
    assert "****" in result.stdout
    assert "admin" in result.stdout
    assert "exists: False" in result.stdout


def test_config_show_masks_password_and_apikey(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hand-written values for sensitive fields aren't leaked to stdout."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text(
        '[server]\nusername = "alice"\npassword = "supersecret"\n\n[acoustid]\napi_key = "topsecretkey"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "alice" in result.stdout
    assert "supersecret" not in result.stdout
    assert "topsecretkey" not in result.stdout
    assert "****" in result.stdout
    assert "exists: True" in result.stdout


def test_config_show_lists_env_overrides(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Active MUSICKIT_* env vars get listed (with their values masked)."""
    _redirect_config(monkeypatch, tmp_path)
    monkeypatch.setenv("MUSICKIT_SERVER__USERNAME", "fromenv")
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "MUSICKIT_SERVER__USERNAME" in result.stdout
    # The env override should propagate to the resolved config too.
    assert "fromenv" in result.stdout


# ---------------------------------------------------------------------------
# `musickit config migrate`
# ---------------------------------------------------------------------------


def test_config_migrate_writes_new_file_and_deletes_legacy(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Legacy serve.toml gets moved into musickit.toml and removed."""
    _redirect_config(monkeypatch, tmp_path)
    legacy = tmp_path / "serve.toml"
    legacy.write_text(
        'username = "morten"\npassword = "wonderful"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "migrate"])
    assert result.exit_code == 0
    assert "Wrote" in result.stdout
    assert (tmp_path / "musickit.toml").exists()
    assert not legacy.exists()


def test_config_migrate_keep_legacy(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--keep-legacy` writes the new file but leaves serve.toml intact."""
    _redirect_config(monkeypatch, tmp_path)
    legacy = tmp_path / "serve.toml"
    legacy.write_text('username = "u"\npassword = "p"\n', encoding="utf-8")
    result = runner.invoke(app, ["config", "migrate", "--keep-legacy"])
    assert result.exit_code == 0
    assert (tmp_path / "musickit.toml").exists()
    assert legacy.exists()
    assert "Kept legacy" in result.stdout


def test_config_migrate_idempotent_when_target_exists(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-running migrate when musickit.toml is present is a no-op."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text("# already migrated\n", encoding="utf-8")
    (tmp_path / "serve.toml").write_text("username = 'x'\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "migrate"])
    assert result.exit_code == 0
    assert "already exists" in result.stdout
    # serve.toml stays untouched in this branch (we only delete on success).
    assert (tmp_path / "serve.toml").exists()


def test_config_migrate_no_legacy_no_op(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No legacy file + no new file → friendly bail, exit 0."""
    _redirect_config(monkeypatch, tmp_path)
    result = runner.invoke(app, ["config", "migrate"])
    assert result.exit_code == 0
    assert "No legacy" in result.stdout


# ---------------------------------------------------------------------------
# Subcommand wiring — `musickit config` (no args) shows the help.
# ---------------------------------------------------------------------------


def test_config_no_args_prints_help(runner: CliRunner) -> None:
    """`musickit config` (no subcommand) prints the help with the three commands."""
    result = runner.invoke(app, ["config"])
    # Typer prints help to stderr / exits 0 with `no_args_is_help=True`.
    assert result.exit_code == 0 or result.exit_code == 2
    out = result.stdout + result.output
    for cmd in ("show", "path", "migrate"):
        assert cmd in out
