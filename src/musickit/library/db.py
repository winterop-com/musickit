"""SQLite-backed library index — schema, connection, version check.

The DB at `<library_root>/.musickit/index.db` is a fully-derived cache of
the filesystem walk + tag read + audit. The filesystem is the source of
truth, so when the schema changes — or when the running musickit version
changes — we unlink the file and rebuild instead of running migrations.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Final

from musickit import __version__ as MUSICKIT_VERSION

log = logging.getLogger(__name__)

SCHEMA_VERSION: Final[int] = 1
"""Bumped when `_SCHEMA` changes shape; mismatched DBs are unlinked + rebuilt."""

INDEX_DIR_NAME: Final[str] = ".musickit"
INDEX_DB_NAME: Final[str] = "index.db"

_SCHEMA: Final[str] = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE albums (
    id               INTEGER PRIMARY KEY,
    rel_path         TEXT NOT NULL UNIQUE,
    artist_dir       TEXT NOT NULL,
    album_dir        TEXT NOT NULL,
    tag_album        TEXT,
    tag_year         TEXT,
    tag_album_artist TEXT,
    tag_genre        TEXT,
    track_count      INTEGER NOT NULL,
    disc_count       INTEGER NOT NULL DEFAULT 1,
    is_compilation   INTEGER NOT NULL DEFAULT 0,
    has_cover        INTEGER NOT NULL DEFAULT 0,
    cover_pixels     INTEGER NOT NULL DEFAULT 0,
    subsonic_id      TEXT,
    dir_mtime        REAL NOT NULL,
    scanned_at       REAL NOT NULL
);
CREATE INDEX idx_albums_artist_dir ON albums(artist_dir);

CREATE TABLE tracks (
    id              INTEGER PRIMARY KEY,
    album_id        INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    rel_path        TEXT NOT NULL UNIQUE,
    title           TEXT,
    artist          TEXT,
    album_artist    TEXT,
    album           TEXT,
    year            TEXT,
    track_no        INTEGER,
    disc_no         INTEGER,
    genre           TEXT,
    lyrics          TEXT,
    replaygain_json TEXT,
    duration_s      REAL NOT NULL DEFAULT 0,
    has_cover       INTEGER NOT NULL DEFAULT 0,
    cover_pixels    INTEGER NOT NULL DEFAULT 0,
    stream_url      TEXT,
    file_mtime      REAL NOT NULL,
    file_size       INTEGER NOT NULL,
    scanned_at      REAL NOT NULL
);
CREATE INDEX idx_tracks_album_id ON tracks(album_id);

CREATE TABLE track_genres (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    genre    TEXT NOT NULL,
    PRIMARY KEY (track_id, genre)
);
CREATE INDEX idx_track_genres_genre ON track_genres(genre);

CREATE TABLE album_warnings (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    warning  TEXT NOT NULL,
    PRIMARY KEY (album_id, warning)
);
"""

_PRAGMAS: Final[tuple[str, ...]] = (
    "journal_mode = WAL",
    "synchronous = NORMAL",
    "foreign_keys = ON",
    "temp_store = MEMORY",
    "mmap_size = 67108864",  # 64 MiB
)


def db_path(root: Path) -> Path:
    """Return `<root>/.musickit/index.db` (the absolute index location)."""
    return root / INDEX_DIR_NAME / INDEX_DB_NAME


def open_db(root: Path) -> sqlite3.Connection:
    """Open or create the index DB for `root`.

    If the existing DB has a stale `schema_version` or was written for a
    different `library_root_abs`, the file (and any WAL sidecars) is
    unlinked and a fresh schema is created.
    """
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and not _can_use_existing(path, root):
        log.info("library index: schema/root/version mismatch at %s; rebuilding", path)
        _unlink_db(path)

    fresh = not path.exists()
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    if fresh:
        _create_schema(conn, root)
    return conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in _PRAGMAS:
        conn.execute(f"PRAGMA {pragma}")


def _create_schema(conn: sqlite3.Connection, root: Path) -> None:
    """Initialise an empty DB with the v1 schema and meta rows.

    `auto_vacuum = INCREMENTAL` is set BEFORE the schema is created — it
    can only be applied to an empty database (or one followed by a full
    `VACUUM`). With this setting, pages freed by `DELETE FROM albums`
    (cascade-deletes tracks / track_genres / album_warnings) and by
    per-album rescans become reclaimable via the cheap `PRAGMA
    incremental_vacuum` we run after each big transaction. Without it,
    fragmented pages just accumulate and the DB grows 2-3× across many
    rescans on a real library.
    """
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("library_root_abs", str(root.resolve())),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("musickit_version", MUSICKIT_VERSION),
    )


def reclaim_freelist(conn: sqlite3.Connection) -> None:
    """Reclaim freed pages on an `auto_vacuum = INCREMENTAL` database.

    Cheap (~ms) when there's nothing to free. Call this after a big
    delete + insert transaction so the file size doesn't accumulate
    fragmented pages over many rescans. Safe even if the DB was created
    with `auto_vacuum = NONE` — incremental_vacuum is a no-op there.
    """
    try:
        conn.execute("PRAGMA incremental_vacuum")
    except sqlite3.DatabaseError:  # pragma: no cover — never raised in practice
        pass


def _can_use_existing(path: Path, root: Path) -> bool:
    """Return True iff the on-disk DB matches the current schema + root."""
    try:
        probe = sqlite3.connect(path, check_same_thread=False)
    except sqlite3.DatabaseError:
        return False
    try:
        try:
            tables = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            return False
        if "meta" not in tables:
            return False
        try:
            schema_row = probe.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            root_row = probe.execute("SELECT value FROM meta WHERE key='library_root_abs'").fetchone()
            version_row = probe.execute("SELECT value FROM meta WHERE key='musickit_version'").fetchone()
        except sqlite3.DatabaseError:
            return False
        if schema_row is None or schema_row[0] != str(SCHEMA_VERSION):
            return False
        if root_row is None or root_row[0] != str(root.resolve()):
            return False
        # version_row may be missing on DBs created before the
        # musickit_version stamp existed — treat absence as a mismatch
        # so they rebuild on first open under a newer musickit.
        if version_row is None or version_row[0] != MUSICKIT_VERSION:
            return False
        return True
    finally:
        probe.close()


def _unlink_db(path: Path) -> None:
    """Unlink the DB file and any WAL/SHM/journal sidecars."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        sidecar.unlink(missing_ok=True)


def is_empty(conn: sqlite3.Connection) -> bool:
    """True when the DB has no album rows yet (fresh schema, never scanned)."""
    row = conn.execute("SELECT 1 FROM albums LIMIT 1").fetchone()
    return row is None
