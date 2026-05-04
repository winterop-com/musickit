"""Hydrate a `LibraryIndex` from the SQLite index DB.

`load(root, conn)` is the read-side counterpart of `scan_full`: same
Pydantic shape, but built from rows instead of from a filesystem walk.
`load_or_scan(root, ...)` is the top-level convenience that opens the
DB, decides between fast load + delta-validate vs. full rescan, and
returns the populated index.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.library.scan import ScanProgressCallback

log = logging.getLogger(__name__)


def load_or_scan(
    root: Path,
    *,
    use_cache: bool = True,
    force: bool = False,
    on_album: ScanProgressCallback | None = None,
    measure_pictures: bool = False,
) -> LibraryIndex:
    """Return a `LibraryIndex` for `root`, using the on-disk cache when available.

    `use_cache=False` skips the DB entirely (in-memory scan + audit). Used
    when `.musickit/` cannot be created (read-only mount) or when the
    caller passes `--no-cache`.

    `force=True` ignores any existing cache and runs a full rescan,
    rewriting every row. Maps to the `--full-rescan` CLI flag and the
    `startScan` Subsonic endpoint.

    Without `force`, a warm cache is loaded and a `validate()` pass
    reconciles the DB against any filesystem-level adds/removes/tag-edits
    that happened while no watcher was running.
    """
    from musickit.library.audit import audit
    from musickit.library.db import open_db
    from musickit.library.scan import scan, scan_full, validate

    if not use_cache:
        index = scan(root, on_album=on_album, measure_pictures=measure_pictures)
        audit(index)
        return index

    try:
        conn = open_db(root)
    except OSError as exc:
        log.warning(
            "library cache disabled: cannot create %s/.musickit (%s); falling back to in-memory scan",
            root,
            exc,
        )
        index = scan(root, on_album=on_album, measure_pictures=measure_pictures)
        audit(index)
        return index

    try:
        from musickit.library.db import is_empty

        if force or is_empty(conn):
            return scan_full(root, conn, on_album=on_album, measure_pictures=measure_pictures)
        result = validate(root, conn, measure_pictures=measure_pictures, on_album=on_album)
        if result:
            log.info(
                "library cache: validated (added=%d removed=%d updated=%d)",
                result.added,
                result.removed,
                result.updated,
            )
        return load(root, conn)
    finally:
        conn.close()


def load(root: Path, conn: sqlite3.Connection) -> LibraryIndex:
    """Build a `LibraryIndex` from the rows currently in `conn`.

    Albums are returned sorted by `(artist_dir, album_dir)` to match the
    order `scan()` produces, so callers can treat the two paths
    interchangeably.
    """
    genres_by_track: dict[int, list[str]] = {}
    for row in conn.execute("SELECT track_id, genre FROM track_genres ORDER BY track_id, genre"):
        genres_by_track.setdefault(row["track_id"], []).append(row["genre"])

    warnings_by_album: dict[int, list[str]] = {}
    for row in conn.execute("SELECT album_id, warning FROM album_warnings ORDER BY album_id, warning"):
        warnings_by_album.setdefault(row["album_id"], []).append(row["warning"])

    tracks_by_album: dict[int, list[LibraryTrack]] = {}
    for row in conn.execute("SELECT * FROM tracks ORDER BY album_id, rel_path"):
        track = LibraryTrack(
            path=root / row["rel_path"],
            title=row["title"],
            artist=row["artist"],
            album_artist=row["album_artist"],
            album=row["album"],
            year=row["year"],
            track_no=row["track_no"],
            disc_no=row["disc_no"],
            genre=row["genre"],
            genres=genres_by_track.get(row["id"], []),
            lyrics=row["lyrics"],
            replaygain=_decode_replaygain(row["replaygain_json"]),
            duration_s=row["duration_s"],
            has_cover=bool(row["has_cover"]),
            cover_pixels=row["cover_pixels"],
            stream_url=row["stream_url"],
        )
        tracks_by_album.setdefault(row["album_id"], []).append(track)

    albums: list[LibraryAlbum] = []
    for row in conn.execute("SELECT * FROM albums ORDER BY LOWER(artist_dir), LOWER(album_dir)"):
        albums.append(
            LibraryAlbum(
                path=root / row["rel_path"],
                artist_dir=row["artist_dir"],
                album_dir=row["album_dir"],
                tag_album=row["tag_album"],
                tag_year=row["tag_year"],
                tag_album_artist=row["tag_album_artist"],
                tag_genre=row["tag_genre"],
                track_count=row["track_count"],
                disc_count=row["disc_count"],
                is_compilation=bool(row["is_compilation"]),
                has_cover=bool(row["has_cover"]),
                cover_pixels=row["cover_pixels"],
                subsonic_id=row["subsonic_id"],
                tracks=tracks_by_album.get(row["id"], []),
                warnings=warnings_by_album.get(row["id"], []),
            )
        )
    return LibraryIndex(root=root, albums=albums)


def _decode_replaygain(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        log.warning("library load: malformed replaygain_json; ignoring")
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(k): str(v) for k, v in decoded.items()}
