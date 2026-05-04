"""Filesystem watcher: debounced auto-rescan on library changes."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from musickit.serve.index import IndexCache
from musickit.serve.watcher import LibraryWatcher


def test_watcher_triggers_rescan_after_audio_file_drop(tmp_path: Path) -> None:
    """An audio file appearing in the library root triggers a debounced rescan."""
    cache = IndexCache(tmp_path)
    cache.start_background_rescan = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.2)
    watcher.start()
    try:
        # Simulate dropping a new track in.
        (tmp_path / "fresh.m4a").write_bytes(b"\x00" * 16)

        # Wait for debounce window to expire + rescan trigger.
        deadline = time.time() + 3.0
        while time.time() < deadline and cache.start_background_rescan.call_count == 0:
            time.sleep(0.05)
        assert cache.start_background_rescan.call_count == 1
    finally:
        watcher.stop()


def test_watcher_debounces_burst_into_single_rescan(tmp_path: Path) -> None:
    """A burst of file drops within the debounce window collapses to one rescan."""
    cache = IndexCache(tmp_path)
    cache.start_background_rescan = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.3)
    watcher.start()
    try:
        for i in range(5):
            (tmp_path / f"track_{i}.flac").write_bytes(b"\x00")
            time.sleep(0.05)  # Each drop is well within the 0.3s debounce.

        # Wait past the debounce window once the burst stops.
        deadline = time.time() + 2.0
        while time.time() < deadline and cache.start_background_rescan.call_count == 0:
            time.sleep(0.05)
        # Give it another half-second to confirm no extras come in.
        time.sleep(0.5)
        assert cache.start_background_rescan.call_count == 1
    finally:
        watcher.stop()


def test_watcher_ignores_non_audio_files(tmp_path: Path) -> None:
    """A `.DS_Store` / cover image drop should NOT trigger a rescan.

    macOS' FSEvents emits a 'history rebuild' marker on observer start
    that can briefly look like activity; settle for 200ms before counting.
    """
    cache = IndexCache(tmp_path)
    cache.start_background_rescan = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.3)
    watcher.start()
    try:
        # Let any FSEvents startup-stabilisation events flush through.
        time.sleep(0.5)
        cache.start_background_rescan.reset_mock()

        (tmp_path / ".DS_Store").write_bytes(b"")
        (tmp_path / "cover.jpg").write_bytes(b"")
        time.sleep(0.7)  # Past the debounce window.
        assert cache.start_background_rescan.call_count == 0
    finally:
        watcher.stop()


def test_watcher_stop_is_safe_when_never_started(tmp_path: Path) -> None:
    """Stopping a watcher before start() must not raise — used in CLI cleanup."""
    cache = IndexCache(tmp_path)
    LibraryWatcher(cache).stop()  # no-op


def test_watcher_skips_when_root_does_not_exist(tmp_path: Path) -> None:
    """Pointing at a non-existent dir is non-fatal; watcher logs + does nothing."""
    cache = IndexCache(tmp_path / "nope")
    watcher = LibraryWatcher(cache)
    watcher.start()  # must not raise
    watcher.stop()
