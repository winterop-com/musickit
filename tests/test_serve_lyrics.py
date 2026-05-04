"""getLyrics + getLyricsBySongId — embedded-lyrics lookup."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.ids import track_id


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


_LYRIC_TEXT = "You can dance, you can jive\nHaving the time of your life"


def _client_with_lyrics(tmp_path: Path) -> tuple[TestClient, LibraryTrack]:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    track = LibraryTrack(
        path=tmp_path / "ABBA" / "Arrival" / "01.m4a",
        title="Dancing Queen",
        artist="ABBA",
        album="Arrival",
        track_no=1,
        duration_s=230.0,
        lyrics=_LYRIC_TEXT,
    )
    album = LibraryAlbum(
        path=tmp_path / "ABBA" / "Arrival",
        artist_dir="ABBA",
        album_dir="Arrival",
        tag_album="Arrival",
        tag_album_artist="ABBA",
        track_count=1,
        tracks=[track],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    return TestClient(app), track


def test_get_lyrics_returns_embedded_lyrics(tmp_path: Path) -> None:
    client, _ = _client_with_lyrics(tmp_path)
    body = client.get("/rest/getLyrics", params=_params(artist="ABBA", title="Dancing Queen")).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["lyrics"]["artist"] == "ABBA"
    assert inner["lyrics"]["title"] == "Dancing Queen"
    assert inner["lyrics"]["value"] == _LYRIC_TEXT


def test_get_lyrics_case_insensitive(tmp_path: Path) -> None:
    client, _ = _client_with_lyrics(tmp_path)
    body = client.get("/rest/getLyrics", params=_params(artist="abba", title="dancing queen")).json()
    assert body["subsonic-response"]["lyrics"]["value"] == _LYRIC_TEXT


def test_get_lyrics_returns_empty_value_when_track_missing(tmp_path: Path) -> None:
    """Spec: empty value, not error 70 — clients show 'no lyrics available'."""
    client, _ = _client_with_lyrics(tmp_path)
    body = client.get("/rest/getLyrics", params=_params(artist="Beck", title="Lost Cause")).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["lyrics"]["value"] == ""


def test_get_lyrics_by_song_id_returns_structured(tmp_path: Path) -> None:
    """OpenSubsonic structured-lyrics shape — line[] per text line."""
    client, track = _client_with_lyrics(tmp_path)
    body = client.get("/rest/getLyricsBySongId", params=_params(id=track_id(track))).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    structured = inner["lyricsList"]["structuredLyrics"]
    assert len(structured) == 1
    entry = structured[0]
    assert entry["displayArtist"] == "ABBA"
    assert entry["displayTitle"] == "Dancing Queen"
    assert entry["synced"] is False
    line_values = [line["value"] for line in entry["line"]]
    assert line_values == _LYRIC_TEXT.splitlines()


def test_get_lyrics_by_song_id_unknown_returns_70(tmp_path: Path) -> None:
    client, _ = _client_with_lyrics(tmp_path)
    body = client.get("/rest/getLyricsBySongId", params=_params(id="tr_doesnotexist")).json()
    assert body["subsonic-response"]["status"] == "failed"
    assert body["subsonic-response"]["error"]["code"] == 70


def test_get_lyrics_by_song_id_no_lyrics_returns_empty_list(tmp_path: Path) -> None:
    """Track exists but has no lyrics → empty structuredLyrics, not error 70."""
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    track = LibraryTrack(
        path=tmp_path / "ABBA" / "Arrival" / "02.m4a",
        title="Money",
        artist="ABBA",
        album="Arrival",
        track_no=2,
        # No lyrics field set.
    )
    album = LibraryAlbum(
        path=tmp_path / "ABBA" / "Arrival",
        artist_dir="ABBA",
        album_dir="Arrival",
        track_count=1,
        tracks=[track],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    body = TestClient(app).get("/rest/getLyricsBySongId", params=_params(id=track_id(track))).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["lyricsList"]["structuredLyrics"] == []
