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


def test_build_index_lazy_returns_shell_albums(tmp_path: Path) -> None:
    """Default (eager=False): albums have IDs + counts but no tracks yet."""
    sc = _subsonic(_serve_test_client(tmp_path))

    index = build_index(sc)

    assert [(a.artist_dir, a.album_dir) for a in index.albums] == [
        ("ABBA", "Arrival"),
        ("Beck", "Sea Change"),
    ]
    arrival = index.albums[0]
    assert arrival.tracks == []
    assert arrival.subsonic_id is not None
    assert arrival.track_count == 2  # from album metadata, not a track walk


def test_build_index_eager_walks_full_library(tmp_path: Path) -> None:
    progress_calls: list[tuple[str, int, int]] = []
    sc = _subsonic(_serve_test_client(tmp_path))

    index = build_index(
        sc,
        eager=True,
        on_progress=lambda name, idx, total: progress_calls.append((name, idx, total)),
    )

    arrival = index.albums[0]
    assert len(arrival.tracks) == 2
    assert all(t.stream_url and t.stream_url.startswith("http://testserver/rest/stream") for t in arrival.tracks)
    assert len(progress_calls) == 2


def test_hydrate_album_tracks_populates_in_place(tmp_path: Path) -> None:
    """Lazy flow: build_index → click album → hydrate fills tracks."""
    from musickit.tui.subsonic_client import hydrate_album_tracks

    sc = _subsonic(_serve_test_client(tmp_path))
    index = build_index(sc)
    arrival = index.albums[0]
    assert arrival.tracks == []

    hydrate_album_tracks(sc, arrival)

    assert len(arrival.tracks) == 2
    assert arrival.track_count == 2
    titles = [t.title for t in arrival.tracks]
    assert titles == ["Dancing Queen", "Money Money Money"]
    assert all(t.stream_url for t in arrival.tracks)


def test_hydrate_album_tracks_idempotent(tmp_path: Path) -> None:
    """Calling hydrate twice doesn't duplicate tracks."""
    from musickit.tui.subsonic_client import hydrate_album_tracks

    sc = _subsonic(_serve_test_client(tmp_path))
    index = build_index(sc)
    arrival = index.albums[0]

    hydrate_album_tracks(sc, arrival)
    hydrate_album_tracks(sc, arrival)
    assert len(arrival.tracks) == 2


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


def test_close_is_idempotent_and_safe(tmp_path: Path) -> None:
    """Calling close() twice (or on a never-used client) must not raise — it runs at app shutdown."""
    sc = _subsonic(_serve_test_client(tmp_path))
    sc.close()
    sc.close()  # second call after the http client is already closed
