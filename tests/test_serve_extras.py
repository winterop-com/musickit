"""Stub endpoints — scrobble / artistInfo / musicDirectory / random / starred / playlists / HEAD."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.ids import album_id, artist_id


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


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
        _album(tmp_path, "Beck", "Sea Change", year="2002", tracks=["The Golden Age"]),
    ]
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


def test_scrobble_returns_ok(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/scrobble", params=_params(id="tr_x", submission="true")).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_get_artist_info2_returns_empty_shell(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getArtistInfo2", params=_params(id="ar_anything")).json()
    info = body["subsonic-response"]["artistInfo2"]
    assert info["biography"] == ""
    assert info["similarArtist"] == []


def test_get_music_directory_for_artist_lists_albums(tmp_path: Path) -> None:
    abba_id = artist_id("ABBA")
    body = _client(tmp_path).get("/rest/getMusicDirectory", params=_params(id=abba_id)).json()
    inner = body["subsonic-response"]["directory"]
    assert inner["name"] == "ABBA"
    assert all(child["isDir"] is True for child in inner["child"])
    names = [c["name"] for c in inner["child"]]
    assert "Arrival" in names


def test_get_music_directory_for_album_lists_songs(tmp_path: Path) -> None:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    fixture_album = _album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["Dancing Queen", "Money Money Money"])
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[fixture_album]))  # noqa: SLF001

    body = TestClient(app).get("/rest/getMusicDirectory", params=_params(id=album_id(fixture_album))).json()
    inner = body["subsonic-response"]["directory"]
    assert inner["name"] == "Arrival"
    assert [c["title"] for c in inner["child"]] == ["Dancing Queen", "Money Money Money"]


def test_get_music_directory_unknown_id_returns_70(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getMusicDirectory", params=_params(id="bogus_xxx")).json()
    assert body["subsonic-response"]["status"] == "failed"
    assert body["subsonic-response"]["error"]["code"] == 70


def test_get_random_songs_returns_at_most_size(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getRandomSongs", params=_params(size=2)).json()
    songs = body["subsonic-response"]["randomSongs"]["song"]
    assert 0 < len(songs) <= 2


def test_get_starred_and_starred2_return_empty(tmp_path: Path) -> None:
    cl = _client(tmp_path)
    s1 = cl.get("/rest/getStarred", params=_params()).json()["subsonic-response"]["starred"]
    s2 = cl.get("/rest/getStarred2", params=_params()).json()["subsonic-response"]["starred2"]
    assert s1 == s2 == {"artist": [], "album": [], "song": []}


def test_star_unstar_no_ops(tmp_path: Path) -> None:
    cl = _client(tmp_path)
    assert cl.get("/rest/star", params=_params(id="tr_x")).json()["subsonic-response"]["status"] == "ok"
    assert cl.get("/rest/unstar", params=_params(id="tr_x")).json()["subsonic-response"]["status"] == "ok"


def test_get_playlists_empty(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getPlaylists", params=_params()).json()
    assert body["subsonic-response"]["playlists"]["playlist"] == []


def test_head_on_ping_returns_200(tmp_path: Path) -> None:
    """play:Sub does HEAD /rest/stream.view to estimate Content-Length; HEAD must be allowed."""
    response = _client(tmp_path).head("/rest/ping", params=_params())
    assert response.status_code == 200


def test_get_user_returns_configured_user_with_all_roles(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getUser", params=_params(username="mort")).json()
    user = body["subsonic-response"]["user"]
    assert user["username"] == "mort"
    assert user["adminRole"] is True
    assert user["streamRole"] is True
    assert user["playlistRole"] is True


def test_get_user_defaults_to_authenticated_username(tmp_path: Path) -> None:
    """Feishin doesn't always send `username=`; fall back to the auth'd user."""
    body = _client(tmp_path).get("/rest/getUser", params=_params()).json()
    assert body["subsonic-response"]["user"]["username"] == "mort"


def test_get_user_unknown_returns_70(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getUser", params=_params(username="someone-else")).json()
    assert body["subsonic-response"]["status"] == "failed"
    assert body["subsonic-response"]["error"]["code"] == 70


def test_get_users_returns_list_of_one(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getUsers", params=_params()).json()
    users = body["subsonic-response"]["users"]["user"]
    assert len(users) == 1
    assert users[0]["username"] == "mort"


def test_get_open_subsonic_extensions_advertises_supported(tmp_path: Path) -> None:
    """Advertise the extensions we actually implement."""
    body = _client(tmp_path).get("/rest/getOpenSubsonicExtensions", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    by_name = {e["name"]: e for e in inner["openSubsonicExtensions"]}
    for required in ("formPost", "transcodeOffset", "multipleGenres", "songLyrics"):
        assert required in by_name, f"missing extension: {required}"
        assert by_name[required]["versions"] == [1]


def test_get_genres_returns_empty_when_no_genre_data(tmp_path: Path) -> None:
    """Library with no genre tags → empty genres list (still 200)."""
    body = _client(tmp_path).get("/rest/getGenres", params=_params()).json()
    assert body["subsonic-response"]["genres"] == {"genre": []}


def test_get_genres_counts_songs_and_albums(tmp_path: Path) -> None:
    """Distinct genres get one entry each with songCount + albumCount."""
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    rock_album = LibraryAlbum(
        path=tmp_path / "Beck" / "Sea Change",
        artist_dir="Beck",
        album_dir="Sea Change",
        tag_album="Sea Change",
        tag_genre="Rock",
        track_count=2,
        tracks=[
            LibraryTrack(
                path=tmp_path / "Beck" / "Sea Change" / "01.m4a",
                title="Lost Cause",
                artist="Beck",
                album="Sea Change",
                genre="Rock",
                track_no=1,
                duration_s=180.0,
            ),
            LibraryTrack(
                path=tmp_path / "Beck" / "Sea Change" / "02.m4a",
                title="The Golden Age",
                artist="Beck",
                album="Sea Change",
                genre="Rock",
                track_no=2,
                duration_s=180.0,
            ),
        ],
    )
    pop_album = LibraryAlbum(
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
            ),
        ],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[rock_album, pop_album]))  # noqa: SLF001

    body = TestClient(app).get("/rest/getGenres", params=_params()).json()
    genres = body["subsonic-response"]["genres"]["genre"]
    by_name = {g["value"]: g for g in genres}
    assert by_name["Rock"]["songCount"] == 2
    assert by_name["Rock"]["albumCount"] == 1
    assert by_name["Pop"]["songCount"] == 1
    assert by_name["Pop"]["albumCount"] == 1


def test_get_genres_counts_each_track_genre_under_multiple_genres(tmp_path: Path) -> None:
    """A track tagged `genres=["Rock", "Indie"]` contributes one song to BOTH
    counts and its album shows up under both album counts.
    Regression: getGenres only consulted `track.genre`, so multi-genre
    tracks were under-counted.
    """
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    album = LibraryAlbum(
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
                genres=["Rock", "Indie"],
                track_no=1,
                duration_s=180.0,
            ),
        ],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    body = TestClient(app).get("/rest/getGenres", params=_params()).json()
    genres = body["subsonic-response"]["genres"]["genre"]
    by_name = {g["value"]: g for g in genres}
    assert "Indie" in by_name, "multi-genre 'Indie' must surface in getGenres"
    assert by_name["Indie"]["songCount"] == 1
    assert by_name["Indie"]["albumCount"] == 1
    assert by_name["Rock"]["songCount"] == 1
    assert by_name["Rock"]["albumCount"] == 1
