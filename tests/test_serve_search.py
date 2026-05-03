"""search3 + search2 — substring + multi-token match across the index."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", **extra}


def _track(album_path: Path, name: str, *, n: int) -> LibraryTrack:
    return LibraryTrack(
        path=album_path / f"{n:02d} - {name}.m4a",
        title=name,
        artist=album_path.parent.name,
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
        tracks=[_track(album_path, name, n=i + 1) for i, name in enumerate(tracks)],
    )


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    albums = [
        _album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["Dancing Queen", "Money Money Money"]),
        _album(tmp_path, "ABBA", "Super Trouper", year="1980", tracks=["Super Trouper", "The Winner Takes It All"]),
        _album(tmp_path, "Beck", "Sea Change", year="2002", tracks=["The Golden Age", "Lost Cause"]),
    ]
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


def test_search3_finds_artist_by_name(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/search3", params=_params(query="abba")).json()["subsonic-response"]
    result = body["searchResult3"]
    assert any(a["name"] == "ABBA" for a in result["artist"])


def test_search3_finds_album_by_partial_name(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/search3", params=_params(query="trouper")).json()["subsonic-response"]
    names = [a["name"] for a in body["searchResult3"]["album"]]
    assert "Super Trouper" in names


def test_search3_finds_song_by_title(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/search3", params=_params(query="dancing queen")).json()["subsonic-response"]
    titles = [s["title"] for s in body["searchResult3"]["song"]]
    assert "Dancing Queen" in titles


def test_search3_multi_token_and_match(tmp_path: Path) -> None:
    """`super trouper` must match — both tokens present — but `super arrival` must not."""
    client = _client(tmp_path)

    body_match = client.get("/rest/search3", params=_params(query="super trouper")).json()["subsonic-response"]
    assert any(a["name"] == "Super Trouper" for a in body_match["searchResult3"]["album"])

    body_no_match = client.get("/rest/search3", params=_params(query="super arrival")).json()["subsonic-response"]
    assert body_no_match["searchResult3"]["album"] == []


def test_search3_empty_query_returns_empty(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/search3", params=_params(query="   ")).json()["subsonic-response"]
    result = body["searchResult3"]
    assert result == {"artist": [], "album": [], "song": []}


def test_search3_pagination(tmp_path: Path) -> None:
    body = (
        _client(tmp_path)
        .get(
            "/rest/search3",
            params=_params(query="the", songCount=1),
        )
        .json()["subsonic-response"]
    )
    assert len(body["searchResult3"]["song"]) <= 1


def test_search2_returns_searchResult2_envelope(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/search2", params=_params(query="abba")).json()["subsonic-response"]
    assert "searchResult2" in body
    assert any(a["name"] == "ABBA" for a in body["searchResult2"]["artist"])
