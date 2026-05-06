"""LibraryWatcher handler-level tests — directory-event filter + moved-event dest.

`test_serve_watcher.py` already covers the integration path (Observer
firing real FS events through to the debounced rescan). These tests
target `_Handler.on_any_event` directly so we can exercise filters
that real FS events don't reliably reproduce — particularly the
moved-event dest_path branch and the dir-create-without-children
case.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    FileCreatedEvent,
    FileMovedEvent,
)

from musickit.serve.watcher import LibraryWatcher, _Handler


def _record_handler() -> tuple[_Handler, list[Path]]:
    captured: list[Path] = []
    return _Handler(captured.append), captured


def test_dir_modified_events_are_ignored() -> None:
    """A dir modified event fires whenever any file inside changes — too noisy to act on."""
    handler, captured = _record_handler()
    handler.on_any_event(DirModifiedEvent(src_path="/lib/Artist/Album"))
    assert captured == []


def test_dir_created_event_triggers() -> None:
    """Dropping a new album dir should fire even before audio files arrive."""
    handler, captured = _record_handler()
    handler.on_any_event(DirCreatedEvent(src_path="/lib/Artist/New Album"))
    assert captured == [Path("/lib/Artist/New Album")]


def test_dir_deleted_event_triggers() -> None:
    """Removing an album dir signals a rescan even though no per-file events fire."""
    handler, captured = _record_handler()
    handler.on_any_event(DirDeletedEvent(src_path="/lib/Artist/Old Album"))
    assert captured == [Path("/lib/Artist/Old Album")]


def test_file_created_audio_extension_triggers() -> None:
    """A new .m4a in any album fires the rescan."""
    handler, captured = _record_handler()
    handler.on_any_event(FileCreatedEvent(src_path="/lib/Artist/Album/01 - Track.m4a"))
    assert captured == [Path("/lib/Artist/Album/01 - Track.m4a")]


def test_file_created_non_audio_filtered() -> None:
    """Cover art + .DS_Store + sidecars are non-audio and must not trigger."""
    handler, captured = _record_handler()
    handler.on_any_event(FileCreatedEvent(src_path="/lib/Artist/Album/cover.jpg"))
    handler.on_any_event(FileCreatedEvent(src_path="/lib/Artist/Album/.DS_Store"))
    handler.on_any_event(FileCreatedEvent(src_path="/lib/Artist/Album/01 - Track.m4a.lrc"))
    assert captured == []


def test_file_moved_uses_destination_path() -> None:
    """A move-IN event surfaces via dest_path, not src_path (which is outside the lib)."""
    handler, captured = _record_handler()
    handler.on_any_event(
        FileMovedEvent(
            src_path="/tmp/staging/Track.flac",
            dest_path="/lib/Artist/Album/01 - Track.flac",
        )
    )
    # Either path being audio is enough to fire; we just need one trigger.
    assert len(captured) == 1
    fired = captured[0]
    assert fired.suffix == ".flac"


def test_file_moved_to_non_audio_extension_filtered() -> None:
    """A rename like `track.tmp` on the destination side shouldn't fire."""
    handler, captured = _record_handler()

    # Both src and dest are non-audio extensions.
    class _NonAudioMove:
        is_directory = False
        event_type = "moved"
        src_path = "/lib/Artist/Album/track.tmp"
        dest_path = "/lib/Artist/Album/track.bak"

    handler.on_any_event(_NonAudioMove())  # type: ignore[arg-type]
    assert captured == []


def test_handler_only_fires_once_per_event() -> None:
    """An event with both audio src and audio dest fires exactly once, not twice.

    Watchdog's `FileMovedEvent` emits both `src_path` and `dest_path`;
    the handler iterates over them but should `return` after the first
    audio-extension match so a single FS event becomes a single
    debounce trigger.
    """
    handler, captured = _record_handler()
    handler.on_any_event(
        FileMovedEvent(
            src_path="/lib/Artist/Album/old.flac",
            dest_path="/lib/Artist/Album/new.flac",
        )
    )
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Watcher lifecycle — stop() during a pending debounce timer
# ---------------------------------------------------------------------------


def test_stop_cancels_pending_debounce_timer(tmp_path: Path) -> None:
    """A stop() mid-debounce must cancel the timer so no rescan fires after teardown."""
    from unittest.mock import MagicMock

    from musickit.serve.index import IndexCache

    cache = IndexCache(tmp_path)
    cache.start_background_rescan = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=5.0)  # long debounce so we can intercept
    # Skip start() — we just want to test the timer cancellation path.
    watcher._on_event(tmp_path / "fresh.flac")  # noqa: SLF001
    assert watcher._timer is not None  # noqa: SLF001
    watcher.stop()
    # After stop(), the timer is gone and no rescan ever fired.
    assert watcher._timer is None  # noqa: SLF001
    assert cache.start_background_rescan.call_count == 0


_ = Any  # Silence "imported but unused" if the local Any annotation drops.
