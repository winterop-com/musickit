"""`musickit serve` CLI flag wiring — `--no-mdns` / `--no-watch` / `--full-rescan`.

The serve command's body ends in a blocking `uvicorn.run(...)`. To
exercise the flag-handling code WITHOUT actually starting an HTTP
server, we mock `uvicorn.run` (returns immediately) and inspect which
components were touched. Three flags under test:

  - `--no-mdns`: skip the mDNS / Bonjour `register_service` call.
  - `--no-watch`: skip the `LibraryWatcher.start()` call.
  - `--full-rescan`: force `IndexCache.rebuild(force=True)` on startup,
    bypassing the SQLite delta-validate fast path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from musickit.cli import app
from tests.test_library import _make_track


def _stage_min_library(tmp_path: Path, silent_flac_template: Path) -> Path:
    """Single-track album so serve has something to scan, fast."""
    root = tmp_path / "lib"
    _make_track(
        root / "Imagine Dragons" / "2012 - Night Visions",
        silent_flac_template,
        filename="01 - Radioactive.m4a",
        title="Radioactive",
    )
    return root


def test_serve_no_mdns_skips_register_service(silent_flac_template: Path, tmp_path: Path) -> None:
    """With `--no-mdns`, `register_service` is never called."""
    root = _stage_min_library(tmp_path, silent_flac_template)

    with (
        patch("uvicorn.run") as uvicorn_mock,
        patch("musickit.serve.discovery.register_service") as register_mock,
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["serve", str(root), "--no-mdns", "--no-watch", "--user", "u", "--password", "p"],
        )
        assert result.exit_code == 0, result.output
        assert uvicorn_mock.called, "uvicorn.run should have been invoked once"
        assert not register_mock.called, "--no-mdns should skip register_service"


def test_serve_no_watch_skips_watcher_start(silent_flac_template: Path, tmp_path: Path) -> None:
    """With `--no-watch`, no `LibraryWatcher` instance is constructed."""
    root = _stage_min_library(tmp_path, silent_flac_template)

    with (
        patch("uvicorn.run") as uvicorn_mock,
        patch("musickit.serve.watcher.LibraryWatcher") as watcher_cls,
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["serve", str(root), "--no-watch", "--no-mdns", "--user", "u", "--password", "p"],
        )
        assert result.exit_code == 0, result.output
        assert uvicorn_mock.called
        assert not watcher_cls.called, "--no-watch should skip LibraryWatcher construction"


def test_serve_default_starts_mdns_and_watcher(silent_flac_template: Path, tmp_path: Path) -> None:
    """Without `--no-*` flags, both mDNS register + watcher start fire."""
    root = _stage_min_library(tmp_path, silent_flac_template)

    register_mock = MagicMock(return_value=None)
    watcher_instance = MagicMock()
    watcher_cls = MagicMock(return_value=watcher_instance)

    with (
        patch("uvicorn.run"),
        patch("musickit.serve.discovery.register_service", register_mock),
        patch("musickit.serve.watcher.LibraryWatcher", watcher_cls),
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["serve", str(root), "--user", "u", "--password", "p"],
        )
        assert result.exit_code == 0, result.output
        assert register_mock.called, "default should call register_service"
        assert watcher_cls.called, "default should construct LibraryWatcher"
        assert watcher_instance.start.called, "watcher should be started"


def test_serve_full_rescan_passes_force_to_rebuild(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--full-rescan` causes `cache.rebuild(force=True)` instead of the delta path."""
    root = _stage_min_library(tmp_path, silent_flac_template)

    rebuild_mock = MagicMock()

    with (
        patch("uvicorn.run"),
        patch(
            "musickit.serve.index.IndexCache.rebuild",
            rebuild_mock,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "serve",
                str(root),
                "--full-rescan",
                "--no-mdns",
                "--no-watch",
                "--user",
                "u",
                "--password",
                "p",
            ],
        )
        assert result.exit_code == 0, result.output
        # `cache.rebuild(on_album=..., force=full_rescan)` is the call
        # site at cli/serve.py:116.
        assert rebuild_mock.called, "rebuild() should be called once on startup"
        kwargs = rebuild_mock.call_args.kwargs
        assert kwargs.get("force") is True, f"expected force=True, got kwargs={kwargs}"
