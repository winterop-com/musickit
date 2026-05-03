"""Cached `LibraryIndex` + reverse-lookup maps for Subsonic IDs.

`IndexCache.rebuild()` runs `library.scan` + `library.audit` and refreshes
every dict. Browsing endpoints resolve IDs against these dicts in O(1).
A `scan_in_progress` flag drives `getScanStatus` and prevents overlapping
rescans; `startScan` spawns a daemon thread that calls `rebuild()`.
"""

from __future__ import annotations

import threading
from pathlib import Path

from musickit import library as library_mod
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve.ids import album_id, artist_id, track_id


class IndexCache:
    """Wraps a `LibraryIndex` with the reverse-lookup maps endpoints need."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.index: LibraryIndex = LibraryIndex(root=root, albums=[])
        self.albums_by_id: dict[str, LibraryAlbum] = {}
        self.tracks_by_id: dict[str, tuple[LibraryAlbum, LibraryTrack]] = {}
        # ar_id → list of albums for that artist (one artist may map to
        # multiple `LibraryAlbum`s — that IS the point).
        self.artists_by_id: dict[str, list[LibraryAlbum]] = {}
        self.artist_name_by_id: dict[str, str] = {}
        self.scan_in_progress: bool = False
        # `_scan_lock` guards `scan_in_progress` flips so two threads can't
        # both think they're the "first" rescan and double-walk the disk.
        self._scan_lock = threading.Lock()

    def rebuild(self) -> None:
        """Scan + audit + repopulate all lookup maps. Blocking."""
        with self._scan_lock:
            if self.scan_in_progress:
                return
            self.scan_in_progress = True
        try:
            new_index = library_mod.scan(self.root)
            library_mod.audit(new_index)
            self._reindex(new_index)
        finally:
            with self._scan_lock:
                self.scan_in_progress = False

    def start_background_rescan(self) -> bool:
        """Kick a daemon thread that calls `rebuild()`. Returns False if a scan is already running."""
        with self._scan_lock:
            if self.scan_in_progress:
                return False
        threading.Thread(target=self.rebuild, name="musickit-rescan", daemon=True).start()
        return True

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

    @property
    def album_count(self) -> int:
        return len(self.albums_by_id)

    @property
    def artist_count(self) -> int:
        return len(self.artists_by_id)

    @property
    def track_count(self) -> int:
        return len(self.tracks_by_id)
