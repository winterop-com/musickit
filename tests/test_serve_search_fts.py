"""FTS5 search index — prefix matching, diacritic folding, multi-token AND, ranking."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app, search_index


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def _track(album_path: Path, name: str, *, n: int, artist: str | None = None) -> LibraryTrack:
    return LibraryTrack(
        path=album_path / f"{n:02d} - {name}.m4a",
        title=name,
        artist=artist or album_path.parent.name,
        album=album_path.name,
        track_no=n,
        duration_s=180.0,
    )


def _album(root: Path, artist: str, album: str, *, year: str, tracks: list[str]) -> LibraryAlbum:
    album_path = root / artist / album
    return LibraryAlbum(
        path=album_path,
        artist_dir=artist,
        album_dir=album,
        tag_album=album,
        tag_year=year,
        tag_album_artist=artist,
        track_count=len(tracks),
        tracks=[_track(album_path, name, n=i + 1, artist=artist) for i, name in enumerate(tracks)],
    )


def _client(tmp_path: Path, albums: list[LibraryAlbum]) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_fts5_available_in_dev_environment() -> None:
    """Sanity check — Python's stdlib SQLite usually ships FTS5 on macOS / Ubuntu."""
    assert search_index.fts5_available() is True


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_prefix_match_finds_partial_token(tmp_path: Path) -> None:
    """`bey` should match `Beyoncé` via FTS5 prefix matching."""
    cl = _client(
        tmp_path,
        [
            _album(tmp_path, "Beyoncé", "Lemonade", year="2016", tracks=["Formation"]),
            _album(tmp_path, "Pixies", "Doolittle", year="1989", tracks=["Debaser"]),
        ],
    )
    body = cl.get("/rest/search3", params=_params(query="bey")).json()
    artists = body["subsonic-response"]["searchResult3"]["artist"]
    assert any(a["name"] == "Beyoncé" for a in artists)


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_strips_diacritics(tmp_path: Path) -> None:
    """`unicode61 remove_diacritics 2` makes `beyonce` match `Beyoncé`."""
    cl = _client(
        tmp_path,
        [_album(tmp_path, "Beyoncé", "Lemonade", year="2016", tracks=["Formation"])],
    )
    body = cl.get("/rest/search3", params=_params(query="beyonce")).json()
    artists = body["subsonic-response"]["searchResult3"]["artist"]
    assert any(a["name"] == "Beyoncé" for a in artists)


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_multi_token_and_match(tmp_path: Path) -> None:
    """`abba 1976` should match the 1976 ABBA album and not the 1981 one."""
    cl = _client(
        tmp_path,
        [
            _album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["Dancing Queen"]),
            _album(tmp_path, "ABBA", "The Visitors", year="1981", tracks=["One Of Us"]),
        ],
    )
    body = cl.get("/rest/search3", params=_params(query="abba 1976")).json()
    albums = body["subsonic-response"]["searchResult3"]["album"]
    names = [a["name"] for a in albums]
    assert "Arrival" in names
    assert "The Visitors" not in names


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_song_match_includes_album_artist(tmp_path: Path) -> None:
    """`yesterday beatles` matches a Beatles song titled 'Yesterday'."""
    cl = _client(
        tmp_path,
        [
            _album(tmp_path, "The Beatles", "Help!", year="1965", tracks=["Yesterday", "Help!"]),
            _album(tmp_path, "Pixies", "Doolittle", year="1989", tracks=["Yesterday's Tears"]),
        ],
    )
    body = cl.get("/rest/search3", params=_params(query="yesterday beatles")).json()
    songs = body["subsonic-response"]["searchResult3"]["song"]
    titles = [s["title"] for s in songs]
    assert "Yesterday" in titles
    # The Pixies song shouldn't match because "beatles" doesn't appear
    # anywhere in its row text.
    assert "Yesterday's Tears" not in titles


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_quote_in_query_does_not_break(tmp_path: Path) -> None:
    """A stray `\"` in the user's query is stripped, not propagated as an FTS5 operator."""
    cl = _client(
        tmp_path,
        [_album(tmp_path, "Pixies", "Doolittle", year="1989", tracks=["Debaser"])],
    )
    body = cl.get("/rest/search3", params=_params(query='deb"aser')).json()
    songs = body["subsonic-response"]["searchResult3"]["song"]
    assert any(s["title"] == "Debaser" for s in songs)


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_empty_query_returns_empty(tmp_path: Path) -> None:
    cl = _client(tmp_path, [_album(tmp_path, "X", "Y", year="2020", tracks=["T"])])
    body = cl.get("/rest/search3", params=_params(query="   ")).json()
    result = body["subsonic-response"]["searchResult3"]
    assert result == {"artist": [], "album": [], "song": []}


@pytest.mark.skipif(not search_index.fts5_available(), reason="SQLite built without FTS5")
def test_search3_zero_count_skips_kind(tmp_path: Path) -> None:
    """`artistCount=0` returns no artists even when the query would match."""
    cl = _client(tmp_path, [_album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["T"])])
    body = cl.get("/rest/search3", params=_params(query="abba", artistCount=0)).json()
    result = body["subsonic-response"]["searchResult3"]
    assert result["artist"] == []
    assert any(a["name"] == "Arrival" for a in result["album"])


def test_query_helper_handles_empty_input() -> None:
    """Direct `search_index.query()` returns [] for empty / whitespace input."""
    if not search_index.fts5_available():
        pytest.skip("SQLite built without FTS5")
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE VIRTUAL TABLE search USING fts5(kind UNINDEXED, sid UNINDEXED, text);")
    assert search_index.query(conn, "", kind="song", limit=10) == []
    assert search_index.query(conn, "   ", kind="song", limit=10) == []
    # limit <= 0 also short-circuits.
    assert search_index.query(conn, "anything", kind="song", limit=0) == []


def test_escape_token_handles_quotes() -> None:
    """`_escape_token` quote-wraps and strips inner quotes."""
    assert search_index._escape_token("foo") == '"foo"'
    assert search_index._escape_token('foo"bar') == '"foobar"'
    # Token consisting only of quotes shrinks to "" — query() filters
    # blanks out before reaching FTS5 so this is harmless.
    assert search_index._escape_token('"') == '""'
