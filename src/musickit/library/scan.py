"""Walk a converted-output directory and build a `LibraryIndex`."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from musickit import naming
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.metadata import SUPPORTED_AUDIO_EXTS, read_source

_ALBUM_DIR_YEAR_RE = re.compile(r"^(\d{4})\s*-\s*(.+)$")

ScanProgressCallback = Callable[[Path, int, int], None]


def scan(
    root: Path,
    *,
    on_album: Callable[[Path, int, int], None] | None = None,
    measure_pictures: bool = False,
) -> LibraryIndex:
    """Walk `root` and build a `LibraryIndex` from every album dir found.

    An album dir = any directory directly containing ≥1 supported audio file.
    Multi-disc albums in this layout are flat (single dir with `01-NN`/`02-NN`
    filenames), so no merge logic is needed here — `convert` already produced
    them that way.

    `on_album(album_dir, idx, total)` is called once per album right before its
    tracks are read, where `idx` is 1-indexed and `total` is the album count
    (known from the cheap pre-walk in `_iter_album_dirs`). The CLI uses this
    to drive a progress bar over slow filesystems / network drives.

    `measure_pictures=True` enables embedded-cover dimension measurement
    (Pillow decode) so the audit's low-res-cover rule has data to work with.
    Adds noticeable scan time per cover, so it's opt-in — used by the CLI's
    `--audit` and `--issues-only` modes.
    """
    album_dirs = _iter_album_dirs(root)
    total = len(album_dirs)
    albums: list[LibraryAlbum] = []
    for idx, album_dir in enumerate(album_dirs, start=1):
        if on_album is not None:
            on_album(album_dir, idx, total)
        album = _scan_album(album_dir, measure_pictures=measure_pictures)
        albums.append(album)
    albums.sort(key=lambda a: (a.artist_dir.lower(), a.album_dir.lower()))
    return LibraryIndex(root=root, albums=albums)


def _iter_album_dirs(root: Path) -> list[Path]:
    """Return every directory under `root` containing ≥1 supported audio file."""
    if not root.is_dir():
        return []
    seen: set[Path] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            continue
        seen.add(path.parent)
    return sorted(seen)


def _scan_album(album_dir: Path, *, measure_pictures: bool = False) -> LibraryAlbum:
    audio_files = sorted(p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS)
    tracks: list[LibraryTrack] = []
    for audio_path in audio_files:
        try:
            source = read_source(audio_path, light=True, measure_pictures=measure_pictures)
        except Exception:
            continue
        track = LibraryTrack(
            path=audio_path,
            title=source.title,
            artist=source.artist,
            album_artist=source.album_artist,
            album=source.album,
            year=_year_only(source.date),
            genre=source.genre,
            genres=list(source.genres),
            lyrics=source.lyrics,
            replaygain=dict(source.replaygain),
            track_no=source.track_no,
            disc_no=source.disc_no,
            duration_s=source.duration_s or 0.0,
            # `light` mode uses a presence-only sentinel (b"") for embedded
            # pictures — skipping the byte copy and Pillow decode but still
            # reporting cover presence accurately.
            has_cover=source.embedded_picture is not None,
            cover_pixels=source.embedded_picture_pixels,
        )
        source.embedded_picture = None
        tracks.append(track)

    artist_dir = album_dir.parent.name
    return LibraryAlbum(
        path=album_dir,
        artist_dir=artist_dir,
        album_dir=album_dir.name,
        tag_album=_majority(t.album for t in tracks),
        tag_year=_majority(t.year for t in tracks),
        tag_album_artist=_majority(t.album_artist for t in tracks),
        tag_genre=_majority(t.genre for t in tracks),
        track_count=len(tracks),
        disc_count=max((t.disc_no or 1 for t in tracks), default=1),
        is_compilation=naming.is_various_artists(artist_dir),
        has_cover=any(t.has_cover for t in tracks),
        cover_pixels=max((t.cover_pixels for t in tracks), default=0),
        tracks=tracks,
        warnings=[],
    )


def _majority(values: Iterable[str | None]) -> str | None:
    counts: Counter[str] = Counter(v for v in values if v)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _year_only(date: str | None) -> str | None:
    if not date:
        return None
    match = re.match(r"(\d{4})", date.strip())
    return match.group(1) if match else None


def _split_dir_year(album_dir: str) -> tuple[str | None, str]:
    """Parse `2012 - Night Visions` into `("2012", "Night Visions")`."""
    match = _ALBUM_DIR_YEAR_RE.match(album_dir)
    if match:
        return match.group(1), match.group(2)
    return None, album_dir


def scan_full(
    root: Path,
    conn: sqlite3.Connection,
    *,
    on_album: ScanProgressCallback | None = None,
    measure_pictures: bool = False,
) -> LibraryIndex:
    """Walk `root`, audit, and write the full result to the index DB.

    Used on cold start when the DB is empty and after a `startScan`. Wipes
    every album/track/genre/warning row first so the DB matches the
    filesystem exactly. Returns the same `LibraryIndex` that callers used
    to get from `scan()` + `audit()`.
    """
    # Imported here to avoid a circular import: audit.py depends on scan.py
    # for `_split_dir_year`, so we can't import it at module scope.
    from musickit.library.audit import audit

    index = scan(root, on_album=on_album, measure_pictures=measure_pictures)
    audit(index)
    write_index(conn, root, index)
    return index


def validate(
    root: Path,
    conn: sqlite3.Connection,
    *,
    measure_pictures: bool = False,
    on_album: ScanProgressCallback | None = None,
) -> "ValidationResult":
    """Diff the filesystem against DB rows and apply add/remove/update deltas.

    Catches changes that happened while no `serve`/watcher was running:
    new albums dropped in, removed albums, tag edits applied with another
    tool. Each affected album is re-scanned in full and re-audited; rows
    for vanished albums are dropped.

    Returns a `ValidationResult` so callers can log a one-line summary.
    """
    fs_album_dirs = {p.resolve() for p in _iter_album_dirs(root)}

    db_track_rows = list(conn.execute("SELECT id, album_id, rel_path, file_mtime, file_size FROM tracks"))
    db_album_rows = list(conn.execute("SELECT id, rel_path FROM albums"))

    db_track_keys = {row["rel_path"]: row for row in db_track_rows}
    db_album_dirs = {(root / row["rel_path"]).resolve(): row for row in db_album_rows}

    affected: set[Path] = set()

    # Albums that vanished entirely → row deletion only, no rescan.
    for db_dir, _row in db_album_dirs.items():
        if db_dir not in fs_album_dirs:
            affected.add(db_dir)

    # New albums on disk that the DB doesn't know about.
    for fs_dir in fs_album_dirs:
        if fs_dir not in db_album_dirs:
            affected.add(fs_dir)

    # For album dirs the DB and FS both know, find tag-edit / file-add /
    # file-remove deltas at the track level.
    fs_audio_by_dir: dict[Path, set[Path]] = {}
    for fs_dir in fs_album_dirs & set(db_album_dirs):
        try:
            fs_audio_by_dir[fs_dir] = {
                p.resolve() for p in fs_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS
            }
        except OSError:
            affected.add(fs_dir)
            continue

    # Build a per-album view of DB tracks for the dirs we still care about.
    db_audio_by_dir: dict[Path, dict[Path, "_TrackRow"]] = {}
    for rel, row in db_track_keys.items():
        abs_path = (root / rel).resolve()
        parent = abs_path.parent
        if parent not in fs_audio_by_dir:
            continue
        db_audio_by_dir.setdefault(parent, {})[abs_path] = row

    for fs_dir, fs_files in fs_audio_by_dir.items():
        db_files = db_audio_by_dir.get(fs_dir, {})
        if set(fs_files) != set(db_files):
            affected.add(fs_dir)
            continue
        for fs_file in fs_files:
            row = db_files[fs_file]
            mtime, size = _safe_stat(fs_file)
            if mtime != row["file_mtime"] or size != row["file_size"]:
                affected.add(fs_dir)
                break

    if not affected:
        return ValidationResult(added=0, removed=0, updated=0)

    return rescan_albums(
        root,
        conn,
        affected,
        measure_pictures=measure_pictures,
        on_album=on_album,
        _db_album_dirs=db_album_dirs,
    )


def rescan_albums(
    root: Path,
    conn: sqlite3.Connection,
    album_dirs: Iterable[Path],
    *,
    measure_pictures: bool = False,
    on_album: ScanProgressCallback | None = None,
    _db_album_dirs: dict[Path, "_AlbumRow"] | None = None,
) -> "ValidationResult":
    """Re-scan + re-audit each album dir; drop rows for any that vanished.

    Reusable by the cold-start `validate()` pass and (in PR 2) the
    filesystem watcher. The DB is updated under one transaction so a
    crash mid-rescan can't half-apply changes.
    """
    from musickit.library.audit import audit_album

    dirs = sorted({p.resolve() for p in album_dirs})
    if _db_album_dirs is None:
        _db_album_dirs = {
            (root / row["rel_path"]).resolve(): row for row in conn.execute("SELECT id, rel_path FROM albums")
        }

    now = time.time()
    root_abs = root.resolve()
    added = removed = updated = 0
    total = len(dirs)

    conn.execute("BEGIN IMMEDIATE")
    try:
        for idx, album_dir in enumerate(dirs, start=1):
            if on_album is not None:
                on_album(album_dir, idx, total)

            existing_row = _db_album_dirs.get(album_dir)
            if existing_row is not None:
                conn.execute("DELETE FROM albums WHERE id = ?", (existing_row["id"],))

            if not album_dir.is_dir():
                if existing_row is not None:
                    removed += 1
                continue

            album = _scan_album(album_dir, measure_pictures=measure_pictures)
            if album.track_count == 0 and existing_row is None:
                # Empty dir that never had a row — nothing to do.
                continue
            audit_album(album)
            new_album_id = _insert_album(conn, root_abs, album, now)
            for track in album.tracks:
                track_id = _insert_track(conn, new_album_id, root_abs, track, now)
                for genre in track.genres:
                    conn.execute(
                        "INSERT OR IGNORE INTO track_genres(track_id, genre) VALUES (?, ?)",
                        (track_id, genre),
                    )
            for warning in album.warnings:
                conn.execute(
                    "INSERT OR IGNORE INTO album_warnings(album_id, warning) VALUES (?, ?)",
                    (new_album_id, warning),
                )

            if existing_row is None:
                added += 1
            else:
                updated += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    # Reclaim pages freed by the per-album DELETE+INSERT cycle. Cheap
    # when there's nothing to free; matters across many rescans.
    if removed or updated:
        from musickit.library.db import reclaim_freelist

        reclaim_freelist(conn)

    return ValidationResult(added=added, removed=removed, updated=updated)


class ValidationResult:
    """Counts returned by `validate()` for one-line logging."""

    __slots__ = ("added", "removed", "updated")

    def __init__(self, *, added: int, removed: int, updated: int) -> None:
        self.added = added
        self.removed = removed
        self.updated = updated

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.updated)

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"ValidationResult(added={self.added}, removed={self.removed}, updated={self.updated})"


# Type aliases for sqlite3.Row hints — Row is duck-typed so a Protocol-ish
# stub would be overkill. These exist only so the helper signatures above
# read cleanly.
_TrackRow = sqlite3.Row
_AlbumRow = sqlite3.Row


def write_index(conn: sqlite3.Connection, root: Path, index: LibraryIndex) -> None:
    """Replace every row in the index DB with the contents of `index`.

    Wraps the writes in a single transaction so a crashed scan leaves the
    previous state intact.
    """
    now = time.time()
    root_abs = root.resolve()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Cascade deletes drop tracks / track_genres / album_warnings too.
        conn.execute("DELETE FROM albums")
        for album in index.albums:
            album_id = _insert_album(conn, root_abs, album, now)
            for track in album.tracks:
                track_id = _insert_track(conn, album_id, root_abs, track, now)
                for genre in track.genres:
                    conn.execute(
                        "INSERT OR IGNORE INTO track_genres(track_id, genre) VALUES (?, ?)",
                        (track_id, genre),
                    )
            for warning in album.warnings:
                conn.execute(
                    "INSERT OR IGNORE INTO album_warnings(album_id, warning) VALUES (?, ?)",
                    (album_id, warning),
                )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('last_full_scan_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now),),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    # Reclaim pages freed by the wipe-and-rewrite — `auto_vacuum =
    # INCREMENTAL` allows the manual `PRAGMA incremental_vacuum` to
    # release them; without this, fragmented pages accumulate across
    # rescans and the file grows 2-3× over time.
    from musickit.library.db import reclaim_freelist

    reclaim_freelist(conn)


def _insert_album(conn: sqlite3.Connection, root_abs: Path, album: LibraryAlbum, now: float) -> int:
    rel = _rel_to_root(album.path, root_abs)
    cursor = conn.execute(
        """
        INSERT INTO albums (
            rel_path, artist_dir, album_dir,
            tag_album, tag_year, tag_album_artist, tag_genre,
            track_count, disc_count, is_compilation,
            has_cover, cover_pixels, subsonic_id,
            dir_mtime, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rel,
            album.artist_dir,
            album.album_dir,
            album.tag_album,
            album.tag_year,
            album.tag_album_artist,
            album.tag_genre,
            album.track_count,
            album.disc_count,
            int(album.is_compilation),
            int(album.has_cover),
            album.cover_pixels,
            album.subsonic_id,
            _safe_mtime(album.path),
            now,
        ),
    )
    rowid = cursor.lastrowid
    if rowid is None:  # pragma: no cover — sqlite3 always populates this on INSERT
        raise RuntimeError(f"failed to insert album {rel!r}")
    return rowid


def _insert_track(
    conn: sqlite3.Connection,
    album_id: int,
    root_abs: Path,
    track: LibraryTrack,
    now: float,
) -> int:
    rel = _rel_to_root(track.path, root_abs)
    mtime, size = _safe_stat(track.path)
    cursor = conn.execute(
        """
        INSERT INTO tracks (
            album_id, rel_path,
            title, artist, album_artist, album, year,
            track_no, disc_no, genre, lyrics, replaygain_json,
            duration_s, has_cover, cover_pixels, stream_url,
            file_mtime, file_size, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            album_id,
            rel,
            track.title,
            track.artist,
            track.album_artist,
            track.album,
            track.year,
            track.track_no,
            track.disc_no,
            track.genre,
            track.lyrics,
            json.dumps(track.replaygain) if track.replaygain else None,
            track.duration_s,
            int(track.has_cover),
            track.cover_pixels,
            track.stream_url,
            mtime,
            size,
            now,
        ),
    )
    rowid = cursor.lastrowid
    if rowid is None:  # pragma: no cover
        raise RuntimeError(f"failed to insert track {rel!r}")
    return rowid


def _rel_to_root(p: Path, root_abs: Path) -> str:
    """Return `p` as a string relative to `root_abs`, falling back to absolute."""
    try:
        return str(p.resolve().relative_to(root_abs))
    except ValueError:
        return str(p)


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _safe_stat(p: Path) -> tuple[float, int]:
    try:
        st = p.stat()
        return st.st_mtime, st.st_size
    except OSError:
        return 0.0, 0
