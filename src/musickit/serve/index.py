"""Cached `LibraryIndex` + reverse-lookup maps for Subsonic IDs.

`IndexCache.rebuild()` calls `library.load_or_scan` (cache-aware) and
refreshes every dict. Browsing endpoints resolve IDs against these dicts
in O(1). A `scan_in_progress` flag drives `getScanStatus` and prevents
overlapping rescans; `startScan` spawns a daemon thread that calls
`rebuild(force=True)` so the watcher / startScan path always rebuilds
the index from scratch.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

from musickit import library as library_mod
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import search_index
from musickit.serve.ids import album_id, artist_id, track_id


class IndexCache:
    """Wraps a `LibraryIndex` with the reverse-lookup maps endpoints need."""

    def __init__(self, root: Path, *, use_cache: bool = True) -> None:
        self.root = root
        self.use_cache = use_cache
        self.index: LibraryIndex = LibraryIndex(root=root, albums=[])
        self.albums_by_id: dict[str, LibraryAlbum] = {}
        self.tracks_by_id: dict[str, tuple[LibraryAlbum, LibraryTrack]] = {}
        # ar_id → list of albums for that artist (one artist may map to
        # multiple `LibraryAlbum`s — that IS the point).
        self.artists_by_id: dict[str, list[LibraryAlbum]] = {}
        self.artist_name_by_id: dict[str, str] = {}
        # In-memory FTS5 search index. Built fresh on every reindex; None
        # when SQLite was compiled without FTS5 (search falls back to the
        # original substring scan).
        self.fts: sqlite3.Connection | None = None
        self.scan_in_progress: bool = False
        # `_scan_lock` guards `scan_in_progress` flips so two threads can't
        # both think they're the "first" rescan and double-walk the disk.
        self._scan_lock = threading.Lock()

    def rebuild(
        self,
        *,
        on_album: Callable[[Path, int, int], None] | None = None,
        force: bool = False,
    ) -> None:
        """Scan + audit + repopulate all lookup maps. Blocking.

        `on_album` is forwarded to `library.load_or_scan` so the CLI can
        drive a progress bar during the initial startup scan or the
        per-album re-validation pass.

        `force=True` bypasses the cache and rewrites every row.
        """
        with self._scan_lock:
            if self.scan_in_progress:
                return
            self.scan_in_progress = True
        try:
            self._rebuild_inner(on_album=on_album, force=force)
        finally:
            with self._scan_lock:
                self.scan_in_progress = False

    def start_background_rescan(self, *, force: bool = True) -> bool:
        """Kick a daemon thread that runs the rebuild. Returns False if already running.

        Sets `scan_in_progress=True` under the lock BEFORE spawning the thread,
        so a `getScanStatus` poll fired right after `startScan` reflects the
        intended active state. Without this the daemon thread could lose the
        race and the client would see `scanning=false` and stop polling.

        `force` defaults to True because the user-triggered `startScan` and
        watcher fallback both want a clean rebuild, not a delta-validate.
        """
        with self._scan_lock:
            if self.scan_in_progress:
                return False
            self.scan_in_progress = True

        def runner() -> None:
            try:
                self._rebuild_inner(force=force)
            finally:
                with self._scan_lock:
                    self.scan_in_progress = False

        threading.Thread(target=runner, name="musickit-rescan", daemon=True).start()
        return True

    def _rebuild_inner(
        self,
        *,
        on_album: Callable[[Path, int, int], None] | None = None,
        force: bool = False,
    ) -> None:
        """Run load_or_scan + reindex. Caller owns `scan_in_progress`."""
        new_index = library_mod.load_or_scan(
            self.root,
            use_cache=self.use_cache,
            force=force,
            on_album=on_album,
        )
        self._reindex(new_index)

    def _reindex(self, idx: LibraryIndex) -> None:
        """Replace all lookup maps from a fresh `LibraryIndex`."""
        albums_by_id: dict[str, LibraryAlbum] = {}
        tracks_by_id: dict[str, tuple[LibraryAlbum, LibraryTrack]] = {}
        artists_by_id: dict[str, list[LibraryAlbum]] = {}
        artist_name_by_id: dict[str, str] = {}
        for album in idx.albums:
            al_id = album_id(album)
            albums_by_id[al_id] = album
            ar_id = artist_id(album.artist_dir)
            artists_by_id.setdefault(ar_id, []).append(album)
            artist_name_by_id[ar_id] = album.artist_dir
            for track in album.tracks:
                tracks_by_id[track_id(track)] = (album, track)
        self.index = idx
        self.albums_by_id = albums_by_id
        self.tracks_by_id = tracks_by_id
        self.artists_by_id = artists_by_id
        self.artist_name_by_id = artist_name_by_id
        # Close the previous FTS connection before swapping it out so we
        # don't leak the old in-memory DB across rebuilds. `build()`
        # returns None if SQLite was compiled without FTS5; that's the
        # signal for `/search3` to fall back to the substring scan.
        if self.fts is not None:
            try:
                self.fts.close()
            except sqlite3.Error:  # pragma: no cover — defensive
                pass
        self.fts = search_index.build(self)

    @property
    def album_count(self) -> int:
        return len(self.albums_by_id)

    @property
    def artist_count(self) -> int:
        return len(self.artists_by_id)

    @property
    def track_count(self) -> int:
        return len(self.tracks_by_id)
