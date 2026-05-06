"""LRCLIB client — happy path, 404, error branches via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from musickit.lyrics import LrcLibClient, LrcLibError


def _client(handler: object) -> LrcLibClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.Client(transport=transport)
    return LrcLibClient(http=http)


def test_happy_path_returns_synced(tmp_path: object) -> None:
    del tmp_path

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/get")
        params = dict(request.url.params)
        assert params["track_name"] == "Dancing Queen"
        assert params["artist_name"] == "ABBA"
        assert params["album_name"] == "Arrival"
        assert params["duration"] == "230"
        return httpx.Response(
            200,
            json={
                "syncedLyrics": "[00:01.00]Friday night and the lights are low",
                "plainLyrics": "Friday night and the lights are low",
            },
        )

    with _client(handler) as c:
        payload = c.get(
            track_name="Dancing Queen",
            artist_name="ABBA",
            album_name="Arrival",
            duration_s=230.0,
        )
    assert payload is not None
    assert "syncedLyrics" in payload
    assert payload["syncedLyrics"].startswith("[00:01.00]")


def test_404_returns_none() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"name": "TrackNotFound"})

    with _client(handler) as c:
        assert c.get(track_name="x", artist_name="y") is None


def test_500_raises_lrcliberror() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _client(handler) as c, pytest.raises(LrcLibError):
        c.get(track_name="x", artist_name="y")


def test_non_json_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>nope</html>")

    with _client(handler) as c, pytest.raises(LrcLibError):
        c.get(track_name="x", artist_name="y")


def test_timeout_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    with _client(handler) as c, pytest.raises(LrcLibError):
        c.get(track_name="x", artist_name="y")


def test_best_lyrics_prefers_synced() -> None:
    c = LrcLibClient()
    payload = {"syncedLyrics": "[00:01.00]hi", "plainLyrics": "hi"}
    assert c.best_lyrics(payload) == "[00:01.00]hi"
    assert c.best_lyrics({"syncedLyrics": "  ", "plainLyrics": "hi"}) == "hi"
    assert c.best_lyrics({}) is None
    c.close()
