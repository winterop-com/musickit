"""--enrich providers (MusicBrainz + Cover Art Archive) with mocked HTTP."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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


def test_acoustid_parses_best_recording_match(tmp_path: Path) -> None:
    """A confident AcoustID hit fills in title + artist from the top recording."""
    from pathlib import Path as _P

    from musickit.enrich import acoustid as acoustid_mod
    from musickit.enrich.acoustid import AcoustIdProvider, FingerprintResult

    payload = {
        "status": "ok",
        "results": [
            {
                "score": 0.97,
                "id": "<acoustid-uuid>",
                "recordings": [
                    {
                        "id": "<recording-mbid>",
                        "title": "Sweet",
                        "artists": [{"id": "<artist-mbid>", "name": "Blockbuster"}],
                    }
                ],
            }
        ],
    }

    def http_handler(request: httpx.Request) -> httpx.Response:
        assert "v2/lookup" in request.url.path
        assert request.url.params.get("client") == "test-key"
        assert "fingerprint" in request.url.params
        return httpx.Response(200, json=payload)

    def fake_fingerprint(_path: _P) -> FingerprintResult:
        return FingerprintResult(fingerprint="AAAA", duration=180.0)

    mock_client = _client_with(http_handler)
    p = AcoustIdProvider("test-key", client=mock_client)
    # Patch the module-level fingerprint() so we don't actually shell out to fpcalc.
    original = acoustid_mod.fingerprint
    acoustid_mod.fingerprint = fake_fingerprint  # type: ignore[assignment]
    try:
        match = p.lookup(tmp_path / "track.mp3")
    finally:
        acoustid_mod.fingerprint = original

    assert match is not None
    assert match.score == 0.97
    assert match.title == "Sweet"
    assert match.artist == "Blockbuster"
    assert match.recording_id == "<recording-mbid>"


def test_acoustid_returns_none_below_confidence_threshold(tmp_path: Path) -> None:
    """A 0.5-confidence hit shouldn't poison title/artist with a weak match."""
    from pathlib import Path as _P

    from musickit.enrich import acoustid as acoustid_mod
    from musickit.enrich.acoustid import AcoustIdProvider, FingerprintResult

    payload = {"status": "ok", "results": [{"score": 0.5, "recordings": [{"title": "Wrong"}]}]}

    def http_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    def fake_fingerprint(_path: _P) -> FingerprintResult:
        return FingerprintResult(fingerprint="AAAA", duration=180.0)

    mock_client = _client_with(http_handler)
    p = AcoustIdProvider("test-key", client=mock_client)
    original = acoustid_mod.fingerprint
    acoustid_mod.fingerprint = fake_fingerprint  # type: ignore[assignment]
    try:
        match = p.lookup(tmp_path / "track.mp3")
    finally:
        acoustid_mod.fingerprint = original

    assert match is None


def test_musichoarders_url_pre_fills_artist_and_album() -> None:
    url = build_search_url("Imagine Dragons", "Night Visions")
    assert "artist=Imagine+Dragons" in url
    assert "album=Night+Visions" in url
    assert url.startswith("https://covers.musichoarders.xyz/")
