"""Filesystem watcher: debounced incremental rescan on library changes."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from musickit.serve.index import IndexCache
from musickit.serve.watcher import LibraryWatcher


def test_watcher_triggers_rescan_after_audio_file_drop(tmp_path: Path) -> None:
    """An audio file appearing in the library root triggers a debounced incremental rescan."""
    cache = IndexCache(tmp_path)
    cache.rescan_paths = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.2)
    watcher.start()
    try:
        # Simulate dropping a new track in.
        (tmp_path / "fresh.m4a").write_bytes(b"\x00" * 16)

        # Wait for debounce window to expire + rescan trigger.
        deadline = time.time() + 3.0
        while time.time() < deadline and cache.rescan_paths.call_count == 0:
            time.sleep(0.05)
        assert cache.rescan_paths.call_count == 1
        # The single passed argument is an iterable of paths containing fresh.m4a.
        passed_paths = set(cache.rescan_paths.call_args.args[0])
        assert any("fresh.m4a" in str(p) for p in passed_paths)
    finally:
        watcher.stop()


def test_watcher_debounces_burst_into_single_rescan(tmp_path: Path) -> None:
    """A burst of file drops within the debounce window collapses to one rescan, with all paths batched."""
    cache = IndexCache(tmp_path)
    cache.rescan_paths = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.3)
    watcher.start()
    try:
        for i in range(5):
            (tmp_path / f"track_{i}.flac").write_bytes(b"\x00")
            time.sleep(0.05)  # Each drop is well within the 0.3s debounce.

        # Wait past the debounce window once the burst stops.
        deadline = time.time() + 2.0
        while time.time() < deadline and cache.rescan_paths.call_count == 0:
            time.sleep(0.05)
        # Give it another half-second to confirm no extras come in.
        time.sleep(0.5)
        assert cache.rescan_paths.call_count == 1
        passed_paths = list(cache.rescan_paths.call_args.args[0])
        # All five tracks should be in the single batched call.
        assert sum(1 for p in passed_paths if "track_" in str(p)) == 5
    finally:
        watcher.stop()


def test_watcher_ignores_non_audio_files(tmp_path: Path) -> None:
    """A `.DS_Store` / cover image drop should NOT trigger a rescan.

    macOS' FSEvents emits a 'history rebuild' marker on observer start
    that can briefly look like activity; settle for 200ms before counting.
    """
    cache = IndexCache(tmp_path)
    cache.rescan_paths = MagicMock()  # type: ignore[method-assign]

    watcher = LibraryWatcher(cache, debounce_s=0.3)
    watcher.start()
    try:
        # Let any FSEvents startup-stabilisation events flush through.
        time.sleep(0.5)
        cache.rescan_paths.reset_mock()

        (tmp_path / ".DS_Store").write_bytes(b"")
        (tmp_path / "cover.jpg").write_bytes(b"")
        time.sleep(0.7)  # Past the debounce window.
        assert cache.rescan_paths.call_count == 0
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


# ---------------------------------------------------------------------------
# IndexCache.rescan_paths — incremental updates
# ---------------------------------------------------------------------------


def test_rescan_paths_picks_up_new_album(silent_flac_template: Path, tmp_path: Path) -> None:
    """Drop a new album in, dispatch its file path, the cache absorbs the album."""
    import shutil

    from tests.test_library import _make_track

    _make_track(tmp_path / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    cache = IndexCache(tmp_path)
    cache.rebuild()
    assert cache.album_count == 1

    new_track = _make_track(tmp_path / "B" / "2021 - Two", silent_flac_template, filename="01 - T.m4a")
    cache.rescan_paths([new_track])
    assert cache.album_count == 2
    assert {a.artist_dir for a in cache.index.albums} == {"A", "B"}

    # Cleanup — silence pytest warning about unused import in some flows.
    del shutil


def test_rescan_paths_drops_removed_album(silent_flac_template: Path, tmp_path: Path) -> None:
    """A vanished album dir → its row is dropped; reverse-lookup dicts stay consistent."""
    import shutil

    from tests.test_library import _make_track

    _make_track(tmp_path / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    album_b = tmp_path / "B" / "2021 - Two"
    _make_track(album_b, silent_flac_template, filename="01 - T.m4a")
    cache = IndexCache(tmp_path)
    cache.rebuild()
    assert cache.album_count == 2

    shutil.rmtree(album_b)
    cache.rescan_paths([album_b])
    assert cache.album_count == 1
    assert cache.index.albums[0].artist_dir == "A"


def test_rescan_paths_rereads_tag_edit(silent_flac_template: Path, tmp_path: Path) -> None:
    """Editing a tag on an existing track → the cached title updates after rescan_paths."""
    import os
    import time

    from mutagen.mp4 import MP4

    from tests.test_library import _make_track

    track = _make_track(tmp_path / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a", title="Old Title")
    cache = IndexCache(tmp_path)
    cache.rebuild()
    assert cache.index.albums[0].tracks[0].title == "Old Title"

    mp4 = MP4(track)
    assert mp4.tags is not None
    mp4.tags["\xa9nam"] = ["New Title"]
    mp4.save()
    # Force an mtime advance even on filesystems with second-resolution stat.
    future = time.time() + 2
    os.utime(track, (future, future))

    cache.rescan_paths([track])
    assert cache.index.albums[0].tracks[0].title == "New Title"


def test_rescan_paths_is_noop_when_paths_outside_root(silent_flac_template: Path, tmp_path: Path) -> None:
    """Watchdog can briefly emit events for sibling dirs; rescan_paths must ignore them."""
    from tests.test_library import _make_track

    _make_track(tmp_path / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    cache = IndexCache(tmp_path)
    cache.rebuild()
    initial_albums = cache.album_count

    cache.rescan_paths([tmp_path.parent / "somewhere-else"])
    assert cache.album_count == initial_albums


def test_rescan_paths_is_noop_when_use_cache_false(silent_flac_template: Path, tmp_path: Path) -> None:
    """In --no-cache mode there's no DB to incrementally update."""
    from tests.test_library import _make_track

    _make_track(tmp_path / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    cache = IndexCache(tmp_path, use_cache=False)
    cache.rebuild()
    initial = cache.album_count

    new_track = _make_track(tmp_path / "B" / "2021 - Two", silent_flac_template, filename="01 - T.m4a")
    cache.rescan_paths([new_track])
    # Without the DB, we can't do incremental updates — full rebuild path
    # is the only refresh option.
    assert cache.album_count == initial
