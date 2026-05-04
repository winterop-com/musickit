"""Cached `LibraryIndex` + reverse-lookup maps for Subsonic IDs.

`IndexCache.rebuild()` calls `library.load_or_scan` (cache-aware) and
refreshes every dict. Browsing endpoints resolve IDs against these
dicts in O(1). A `scan_in_progress` flag drives `getScanStatus` and
prevents overlapping rescans; `startScan` spawns a daemon thread that
calls `rebuild(force=True)`.

`IndexCache.rescan_paths(paths)` is the watcher-driven incremental path:
it maps each path to its album dir, asks `library.rescan_albums` to
re-scan only those albums (writing the deltas to the SQLite index in
one transaction), then refreshes the in-memory `LibraryIndex` and
reverse-lookup dicts. No full rebuild; affected albums get spliced in
place.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from musickit import library as library_mod
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve.ids import album_id, artist_id, track_id

log = logging.getLogger(__name__)


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

    def rescan_paths(self, paths: Iterable[Path]) -> None:
        """Incrementally re-scan only the albums affected by `paths`.

        Each input path is resolved to its album directory (the path
        itself if it's already a dir, else its parent). Vanished album
        dirs are dropped from the DB; surviving ones are re-read + re-
        audited via `library.rescan_albums`. Finally the in-memory
        `LibraryIndex` and reverse-lookup dicts are refreshed from the
        current DB rows so endpoints see consistent state.

        No-op when `use_cache=False` — without the SQLite index there's
        nothing to incrementally update; full rebuilds remain the only
        option in that mode.
        """
        if not self.use_cache:
            log.debug("rescan_paths: cache disabled; ignoring incremental rescan")
            return

        album_dirs = self._album_dirs_for(paths)
        if not album_dirs:
            return

        with self._scan_lock:
            if self.scan_in_progress:
                # A full rebuild is already running; let it cover the deltas.
                log.debug("rescan_paths: full rebuild in progress; skipping incremental")
                return
            self.scan_in_progress = True
        try:
            conn = library_mod.open_db(self.root)
            try:
                result = library_mod.rescan_albums(self.root, conn, album_dirs)
                refreshed = library_mod.load(self.root, conn)
            finally:
                conn.close()
            self._reindex(refreshed)
            log.info(
                "rescan_paths: %d album dir(s) processed (added=%d removed=%d updated=%d)",
                len(album_dirs),
                result.added,
                result.removed,
                result.updated,
            )
        except Exception:
            log.exception("rescan_paths: incremental rescan failed; index may be stale")
        finally:
            with self._scan_lock:
                self.scan_in_progress = False

    def _album_dirs_for(self, paths: Iterable[Path]) -> set[Path]:
        """Resolve `paths` to the album dirs that need re-scanning.

        Three cases per input path:

        - Existing directory → use as-is (it might be an album dir or an
          artist dir; `library.rescan_albums` silently no-ops on dirs
          without audio files, so the artist-dir case is harmless).
        - Existing file → use its parent (the album dir that contains it).
        - Vanished path → add BOTH the path itself and its parent. We
          can't tell from the path alone whether a deleted thing was a
          file or a dir; `library.rescan_albums` drops the album row for
          a vanished dir and ignores a vanished file path.

        Paths outside `self.root` are dropped — watchdog can briefly
        emit events for sibling dirs while observers shut down. The
        library root itself is also dropped; only its descendants
        correspond to album rows.
        """
        root_abs = self.root.resolve()
        album_dirs: set[Path] = set()
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            try:
                resolved.relative_to(root_abs)
            except ValueError:
                continue
            if resolved == root_abs:
                continue
            if resolved.is_dir():
                album_dirs.add(resolved)
            elif resolved.exists():
                if resolved.parent != root_abs:
                    album_dirs.add(resolved.parent)
            else:
                # Vanished — could be a file or a dir. Add both so
                # rescan_albums gets a shot at each.
                if resolved.parent != root_abs:
                    album_dirs.add(resolved.parent)
                album_dirs.add(resolved)
        return album_dirs

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
