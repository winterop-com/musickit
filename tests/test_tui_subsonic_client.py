"""SubsonicClient + build_index — round-tripped against a real `serve` TestClient."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.tui.subsonic_client import SubsonicClient, SubsonicError, build_index


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


def _serve_test_client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    albums = [
        _album(tmp_path, "ABBA", "Arrival", year="1976", tracks=["Dancing Queen", "Money Money Money"]),
        _album(tmp_path, "Beck", "Sea Change", year="2002", tracks=["The Golden Age", "Lost Cause"]),
    ]
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


def _subsonic(test_client: TestClient) -> SubsonicClient:
    """Wrap a serve TestClient as a SubsonicClient — TestClient is httpx-compatible."""
    return SubsonicClient(
        base_url="http://testserver",
        user="mort",
        password="secret",
        http=test_client,
    )


def test_ping_round_trips(tmp_path: Path) -> None:
    sc = _subsonic(_serve_test_client(tmp_path))
    sc.ping()  # no exception = pass


def test_ping_wrong_password_raises(tmp_path: Path) -> None:
    server = _serve_test_client(tmp_path)
    sc = SubsonicClient("http://testserver", "mort", "wrong", http=server)
    with pytest.raises(SubsonicError):
        sc.ping()


def test_get_artists_returns_two(tmp_path: Path) -> None:
    sc = _subsonic(_serve_test_client(tmp_path))
    artists = sc.get_artists()
    names = {a["name"] for a in artists}
    assert names == {"ABBA", "Beck"}


def test_build_index_walks_full_library(tmp_path: Path) -> None:
    progress_calls: list[tuple[str, int, int]] = []
    sc = _subsonic(_serve_test_client(tmp_path))

    index = build_index(sc, on_progress=lambda name, idx, total: progress_calls.append((name, idx, total)))

    # Ordered by (artist, album).
    assert [(a.artist_dir, a.album_dir) for a in index.albums] == [
        ("ABBA", "Arrival"),
        ("Beck", "Sea Change"),
    ]
    # Each album has its tracks populated with stream URLs ready for AudioPlayer.
    arrival = index.albums[0]
    assert len(arrival.tracks) == 2
    assert all(t.stream_url and t.stream_url.startswith("http://testserver/rest/stream") for t in arrival.tracks)
    # Progress callback fired once per album with rising idx + correct total.
    assert len(progress_calls) == 2
    assert [c[1] for c in progress_calls] == [1, 2]
    assert {c[2] for c in progress_calls} == {2}


def test_stream_url_includes_auth_params(tmp_path: Path) -> None:
    sc = _subsonic(_serve_test_client(tmp_path))
    url = sc.stream_url("tr_abc")
    # Auth + version + client + format must all be present so the server accepts it.
    assert "u=mort" in url
    assert "p=secret" in url
    assert "v=1.16.1" in url
    assert "c=musickit-tui" in url
    assert "id=tr_abc" in url


def test_cover_url_with_size(tmp_path: Path) -> None:
    sc = _subsonic(_serve_test_client(tmp_path))
    url = sc.cover_url("al_xxx", size=300)
    assert "id=al_xxx" in url
    assert "size=300" in url
