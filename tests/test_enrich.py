"""--enrich providers (MusicBrainz + Cover Art Archive) with mocked HTTP."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from musickit.enrich.coverart import CoverArtArchiveProvider
from musickit.enrich.musicbrainz import MusicBrainzProvider
from musickit.enrich.musichoarders import build_search_url
from musickit.metadata import AlbumSummary

Handler = Callable[[httpx.Request], httpx.Response]


def _client_with(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_musicbrainz_returns_top_release_when_score_above_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/release/")
        assert "release:" in request.url.params.get("query", "")
        return httpx.Response(
            200,
            json={
                "releases": [
                    {"id": "abc-123", "score": 100, "title": "Night Visions"},
                    {"id": "def-456", "score": 80},
                ]
            },
        )

    client = _client_with(handler)
    summary = AlbumSummary(album="Night Visions", album_artist="Imagine Dragons", year="2012")
    result = MusicBrainzProvider(client=client).enrich(summary, [])
    assert result.musicbrainz is not None
    assert result.musicbrainz.album_id == "abc-123"


def test_musicbrainz_skips_low_confidence_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"releases": [{"id": "abc-123", "score": 70}]})

    client = _client_with(handler)
    summary = AlbumSummary(album="Some Album", album_artist="Some Artist")
    result = MusicBrainzProvider(client=client).enrich(summary, [])
    assert result.musicbrainz is None
    assert any("no release matched" in n for n in result.notes)


def test_musicbrainz_handles_va_compilations() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.params.get("query", "")
        return httpx.Response(200, json={"releases": []})

    client = _client_with(handler)
    summary = AlbumSummary(album="Mix", album_artist="VA", is_compilation=True)
    MusicBrainzProvider(client=client).enrich(summary, [])
    assert "Various Artists" in captured["query"]


def test_coverartarchive_fetch_returns_candidate_on_hit() -> None:
    from io import BytesIO

    from PIL import Image

    # Real (tiny) JPEG so the decode-and-measure step inside fetch() succeeds.
    img = Image.new("RGB", (40, 40), color=(120, 80, 200))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    cover_bytes = buf.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "release/abc-123/front-1200" in request.url.path
        return httpx.Response(200, content=cover_bytes, headers={"content-type": "image/jpeg"})

    client = _client_with(handler)
    result = CoverArtArchiveProvider(client=client).fetch("abc-123")
    assert len(result.extra_covers) == 1
    assert result.extra_covers[0].data == cover_bytes
    assert result.extra_covers[0].mime == "image/jpeg"
    assert result.extra_covers[0].source.value == "online"
    assert result.extra_covers[0].width == 40 and result.extra_covers[0].height == 40


def test_coverartarchive_fetch_returns_empty_on_5xx() -> None:
    """Internet Archive's CDN occasionally 500s; we must not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with(handler)
    result = CoverArtArchiveProvider(client=client).fetch("flaky-mbid")
    assert result.extra_covers == []
    assert any("HTTP 500" in n for n in result.notes)


def test_coverartarchive_fetch_returns_empty_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client_with(handler)
    result = CoverArtArchiveProvider(client=client).fetch("missing-mbid")
    assert result.extra_covers == []
    assert any("no art" in n for n in result.notes)


def test_musichoarders_url_pre_fills_artist_and_album() -> None:
    url = build_search_url("Imagine Dragons", "Night Visions")
    assert "artist=Imagine+Dragons" in url
    assert "album=Night+Visions" in url
    assert url.startswith("https://covers.musichoarders.xyz/")
