"""FTS5-backed search index — in-memory, rebuilt on every cache reindex.

The previous `/search2` and `/search3` implementations were Python
list comprehensions over `cache.albums_by_id` / `cache.tracks_by_id`
with casefolded `in` checks. On a 23k-track library that's ~30-60ms
per query, growing linearly with the catalogue. This module builds an
SQLite FTS5 virtual table at reindex time and queries it via
`MATCH` for sub-millisecond ranked results.

Why in-memory (`:memory:`) instead of persisting in `index.db`:

  - The FTS rows are fully derived from cache state, not user data —
    no need to survive restarts. Rebuilding from a populated cache
    takes <500ms on 23k tracks, well below the cost of one cold-start
    scan.
  - Avoids a SQLite schema bump (which would unlink the index DB and
    force a full filesystem walk).
  - Decouples search from the persistent index — `--no-cache` mode
    still gets fast search.

The schema is one virtual table with `kind` UNINDEXED + `text`
indexed. The `kind` column is a literal `'artist'` / `'album'` /
`'song'` so a single query can return all three with one MATCH.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from musickit.serve.index import IndexCache

log = logging.getLogger(__name__)

# `unicode61 remove_diacritics 2` makes "beyonce" match "Beyoncé" and
# vice versa — Mac-tagged libraries are full of accented characters
# that a plain ASCII tokenizer would miss.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE search USING fts5(
    kind UNINDEXED,
    sid  UNINDEXED,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def fts5_available() -> bool:
    """Return True iff this Python's SQLite was compiled with FTS5.

    macOS / Ubuntu ship Python with FTS5 enabled; some custom builds
    omit it. We fall back to the substring scan when FTS5 is missing.
    """
    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute("CREATE VIRTUAL TABLE _t USING fts5(x);")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            probe.close()
    except sqlite3.Error:  # pragma: no cover — sqlite3 missing entirely
        return False


def build(cache: IndexCache) -> sqlite3.Connection | None:
    """Build a fresh in-memory FTS5 index from the cache. Returns None when unsupported."""
    if not fts5_available():
        return None
    conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    conn.executescript(_FTS_SCHEMA)

    rows: list[tuple[str, str, str]] = []
    for ar_id, name in cache.artist_name_by_id.items():
        rows.append(("artist", ar_id, name))
    for al_id, album in cache.albums_by_id.items():
        title = album.tag_album or album.album_dir
        # Include album_artist + year in the body so "abba 1976" matches
        # "Arrival" by ABBA released 1976.
        body = " ".join(
            t
            for t in (
                title,
                album.tag_album_artist,
                album.artist_dir,
                album.tag_year,
            )
            if t
        )
        rows.append(("album", al_id, body))
    for tr_id, (album, track) in cache.tracks_by_id.items():
        title = track.title or track.path.stem
        # Title + artist + album so a song row matches both kinds of
        # queries: "yesterday" or "yesterday beatles".
        body = " ".join(
            t
            for t in (
                title,
                track.artist,
                track.album_artist,
                album.tag_album,
                album.artist_dir,
            )
            if t
        )
        rows.append(("song", tr_id, body))

    with conn:
        conn.executemany("INSERT INTO search(kind, sid, text) VALUES (?, ?, ?)", rows)
    log.debug("fts5 search index built: %d rows", len(rows))
    return conn


def query(
    conn: sqlite3.Connection,
    user_query: str,
    *,
    kind: str,
    limit: int,
    offset: int = 0,
) -> list[str]:
    """Return Subsonic IDs matching `user_query`, ranked by FTS bm25.

    `kind` filters to `'artist'` / `'album'` / `'song'`. `user_query`
    is a casefolded space-separated multi-token expression — each token
    is converted to a prefix match (`token*`) so partial typing
    (`bey` matches `Beyoncé`) and AND-combined (every token must
    match somewhere in the row's text).
    """
    if limit <= 0 or not user_query.strip():
        return []
    tokens = [_escape_token(tok) for tok in user_query.split() if tok]
    if not tokens:
        return []
    match_expr = " AND ".join(f"{tok}*" for tok in tokens)
    rows = conn.execute(
        "SELECT sid FROM search WHERE kind = ? AND search MATCH ? ORDER BY bm25(search) ASC LIMIT ? OFFSET ?",
        (kind, match_expr, limit, offset),
    ).fetchall()
    return [r[0] for r in rows]


def _escape_token(tok: str) -> str:
    """Quote-wrap a single FTS5 token so user input can't break out into operators.

    Strips double quotes from the token and wraps the result in double
    quotes — the FTS5 phrase syntax. This makes `"foo"` a literal match
    on `foo` and prevents a stray `"` from splitting one token into two
    or breaking out into FTS5 operators (AND / OR / NEAR / NOT).
    """
    safe = tok.replace('"', "")
    return f'"{safe}"'
