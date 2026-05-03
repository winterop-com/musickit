"""Browsing endpoint round-trips against a synthetic LibraryIndex."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.ids import album_id, artist_id, track_id


def _track(album_path: Path, name: str, *, n: int, duration: float = 180.0, year: str = "2012") -> LibraryTrack:
    return LibraryTrack(
        path=album_path / f"{n:02d} - {name}.m4a",
        title=name,
        artist=album_path.parent.name,
        album=album_path.name,
        year=year,
        track_no=n,
        duration_s=duration,
        has_cover=True,
        cover_pixels=600 * 600,
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
        has_cover=True,
        cover_pixels=600 * 600,
        tracks=[_track(album_path, name, n=i + 1, year=year) for i, name in enumerate(tracks)],
    )


def _client_with_index(tmp_path: Path, albums: list[LibraryAlbum]) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    # Inject the synthetic index directly — no disk walk in tests.
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _two_artists(tmp_path: Path) -> list[LibraryAlbum]:
    return [
        _album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["Dancing Queen", "Money Money Money"]),
        _album(tmp_path, "ABBA", "Super Trouper", year="1980", tracks=["Super Trouper", "The Winner Takes It All"]),
        _album(tmp_path, "Beck", "Sea Change", year="2002", tracks=["The Golden Age", "Lost Cause"]),
    ]


# ---------------------------------------------------------------------------
# getArtists / getIndexes
# ---------------------------------------------------------------------------


def test_get_artists_groups_alphabetically(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get("/rest/getArtists", params=_params()).json()["subsonic-response"]
    assert body["status"] == "ok"
    index = body["artists"]["index"]
    letters = [bucket["name"] for bucket in index]
    assert letters == ["A", "B"]
    abba_bucket = next(b for b in index if b["name"] == "A")["artist"]
    assert len(abba_bucket) == 1
    assert abba_bucket[0]["name"] == "ABBA"
    assert abba_bucket[0]["albumCount"] == 2


def test_get_artists_returns_ignored_articles(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get("/rest/getArtists", params=_params()).json()["subsonic-response"]
    assert "The" in body["artists"]["ignoredArticles"]


def test_get_indexes_same_data_legacy_envelope(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get("/rest/getIndexes", params=_params()).json()["subsonic-response"]
    assert body["status"] == "ok"
    assert "indexes" in body
    assert len(body["indexes"]["index"]) == 2


# ---------------------------------------------------------------------------
# getArtist
# ---------------------------------------------------------------------------


def test_get_artist_returns_albums_chronologically(tmp_path: Path) -> None:
    albums = _two_artists(tmp_path)
    client = _client_with_index(tmp_path, albums)
    abba_id = artist_id("ABBA")
    body = client.get("/rest/getArtist", params=_params(id=abba_id)).json()["subsonic-response"]
    assert body["status"] == "ok"
    artist = body["artist"]
    assert artist["name"] == "ABBA"
    assert artist["albumCount"] == 2
    names = [a["name"] for a in artist["album"]]
    assert names == ["Arrival", "Super Trouper"]


def test_get_artist_unknown_id_returns_70(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get("/rest/getArtist", params=_params(id="ar_doesnotexist")).json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 70


# ---------------------------------------------------------------------------
# getAlbum
# ---------------------------------------------------------------------------


def test_get_album_returns_tracks_with_correct_metadata(tmp_path: Path) -> None:
    albums = _two_artists(tmp_path)
    arrival = albums[0]
    client = _client_with_index(tmp_path, albums)
    al_id = album_id(arrival)
    body = client.get("/rest/getAlbum", params=_params(id=al_id)).json()["subsonic-response"]
    assert body["status"] == "ok"
    album = body["album"]
    assert album["name"] == "Arrival"
    assert album["artist"] == "ABBA"
    assert album["year"] == 1976
    assert album["songCount"] == 2
    songs = album["song"]
    assert len(songs) == 2
    first = songs[0]
    assert first["title"] == "Dancing Queen"
    assert first["track"] == 1
    assert first["suffix"] == "m4a"
    assert first["contentType"] == "audio/mp4"
    assert first["albumId"] == al_id


def test_get_album_unknown_id_returns_70(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get("/rest/getAlbum", params=_params(id="al_nope")).json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 70


# ---------------------------------------------------------------------------
# getSong
# ---------------------------------------------------------------------------


def test_get_song_returns_track_payload(tmp_path: Path) -> None:
    albums = _two_artists(tmp_path)
    arrival = albums[0]
    track = arrival.tracks[0]
    client = _client_with_index(tmp_path, albums)
    body = client.get("/rest/getSong", params=_params(id=track_id(track))).json()["subsonic-response"]
    assert body["status"] == "ok"
    assert body["song"]["title"] == "Dancing Queen"
    assert body["song"]["artistId"] == artist_id("ABBA")


# ---------------------------------------------------------------------------
# getAlbumList2
# ---------------------------------------------------------------------------


def test_get_album_list2_alphabetical_by_name(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="alphabeticalByName", size=10),
    ).json()["subsonic-response"]
    names = [a["name"] for a in body["albumList2"]["album"]]
    assert names == ["Arrival", "Sea Change", "Super Trouper"]


def test_get_album_list2_alphabetical_by_artist(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="alphabeticalByArtist", size=10),
    ).json()["subsonic-response"]
    pairs = [(a["artist"], a["name"]) for a in body["albumList2"]["album"]]
    assert pairs == [("ABBA", "Arrival"), ("ABBA", "Super Trouper"), ("Beck", "Sea Change")]


def test_get_album_list2_pagination(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="alphabeticalByName", size=2, offset=1),
    ).json()["subsonic-response"]
    names = [a["name"] for a in body["albumList2"]["album"]]
    assert names == ["Sea Change", "Super Trouper"]


def test_get_album_list2_by_year_filters_and_orders(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="byYear", fromYear=1970, toYear=1990, size=10),
    ).json()["subsonic-response"]
    pairs = [(a["name"], a["year"]) for a in body["albumList2"]["album"]]
    assert pairs == [("Arrival", 1976), ("Super Trouper", 1980)]


def test_get_album_list2_by_year_descending(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="byYear", fromYear=2010, toYear=1970, size=10),
    ).json()["subsonic-response"]
    years = [a["year"] for a in body["albumList2"]["album"]]
    assert years == sorted(years, reverse=True)


def test_get_album_list2_by_genre_filters_correctly(tmp_path: Path) -> None:
    """byGenre must filter by track/album genre, not album_artist (which was the old bug)."""
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    rock = LibraryAlbum(
        path=tmp_path / "Beck" / "Sea Change",
        artist_dir="Beck",
        album_dir="Sea Change",
        tag_album="Sea Change",
        tag_genre="Rock",
        track_count=1,
        tracks=[
            LibraryTrack(
                path=tmp_path / "Beck" / "Sea Change" / "01.m4a",
                title="Lost Cause",
                artist="Beck",
                album="Sea Change",
                genre="Rock",
                track_no=1,
                duration_s=180.0,
            )
        ],
    )
    pop = LibraryAlbum(
        path=tmp_path / "ABBA" / "Arrival",
        artist_dir="ABBA",
        album_dir="Arrival",
        tag_album="Arrival",
        tag_genre="Pop",
        track_count=1,
        tracks=[
            LibraryTrack(
                path=tmp_path / "ABBA" / "Arrival" / "01.m4a",
                title="Dancing Queen",
                artist="ABBA",
                album="Arrival",
                genre="Pop",
                track_no=1,
                duration_s=180.0,
            )
        ],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[rock, pop]))  # noqa: SLF001

    body = (
        TestClient(app)
        .get(
            "/rest/getAlbumList2",
            params=_params(type="byGenre", genre="Rock", size=10),
        )
        .json()
    )
    names = [a["name"] for a in body["subsonic-response"]["albumList2"]["album"]]
    assert names == ["Sea Change"]


def test_album_payload_includes_genre(tmp_path: Path) -> None:
    """getAlbum response carries the genre field per Subsonic spec."""
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    rock_album = LibraryAlbum(
        path=tmp_path / "Beck" / "Sea Change",
        artist_dir="Beck",
        album_dir="Sea Change",
        tag_album="Sea Change",
        tag_genre="Rock",
        track_count=1,
        tracks=[
            LibraryTrack(
                path=tmp_path / "Beck" / "Sea Change" / "01.m4a",
                title="Lost Cause",
                artist="Beck",
                album="Sea Change",
                genre="Rock",
                track_no=1,
                duration_s=180.0,
            )
        ],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[rock_album]))  # noqa: SLF001
    al_id = album_id(rock_album)
    body = TestClient(app).get("/rest/getAlbum", params=_params(id=al_id)).json()
    album = body["subsonic-response"]["album"]
    assert album["genre"] == "Rock"
    assert album["song"][0]["genre"] == "Rock"


def test_get_album_list2_unknown_type_falls_back_to_alphabetical(tmp_path: Path) -> None:
    client = _client_with_index(tmp_path, _two_artists(tmp_path))
    body = client.get(
        "/rest/getAlbumList2",
        params=_params(type="newest", size=10),
    ).json()["subsonic-response"]
    assert body["status"] == "ok"
    assert len(body["albumList2"]["album"]) == 3
