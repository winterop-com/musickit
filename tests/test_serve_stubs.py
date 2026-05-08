"""Stub endpoints — confirm every common Subsonic-client probe gets a 200 + ok envelope."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


# Endpoints the stubs module covers + the empty-payload envelope key
# clients expect (None = no payload key, just the bare ok envelope).
STUB_ENDPOINTS: list[tuple[str, str | None]] = [
    ("/getPodcasts", "podcasts"),
    ("/getNewestPodcasts", "newestPodcasts"),
    ("/refreshPodcasts", None),
    ("/createPodcastChannel", None),
    ("/deletePodcastChannel", None),
    ("/deletePodcastEpisode", None),
    ("/downloadPodcastEpisode", None),
    ("/getBookmarks", "bookmarks"),
    ("/createBookmark", None),
    ("/deleteBookmark", None),
    ("/getPlayQueue", None),
    ("/savePlayQueue", None),
    ("/getShares", "shares"),
    ("/createShare", "shares"),
    ("/updateShare", None),
    ("/deleteShare", None),
    ("/getInternetRadioStations", "internetRadioStations"),
    ("/createInternetRadioStation", None),
    ("/updateInternetRadioStation", None),
    ("/deleteInternetRadioStation", None),
    ("/getChatMessages", "chatMessages"),
    ("/addChatMessage", None),
    ("/jukeboxControl", "jukeboxStatus"),
    ("/getSimilarSongs", "similarSongs"),
    ("/getSimilarSongs2", "similarSongs2"),
    ("/getTopSongs", "topSongs"),
    ("/getAlbumInfo", "albumInfo"),
    ("/getAlbumInfo2", "albumInfo"),
    ("/getNowPlaying", "nowPlaying"),
    ("/setRating", None),
    ("/changePassword", None),
    ("/createUser", None),
    ("/updateUser", None),
    ("/deleteUser", None),
    ("/getAvatar", None),
]


@pytest.mark.parametrize(("path", "payload_key"), STUB_ENDPOINTS)
def test_stub_endpoint_returns_ok_envelope(tmp_path: Path, path: str, payload_key: str | None) -> None:
    """Every stub returns 200 + a Subsonic ok envelope so clients never log a 404."""
    body = _client(tmp_path).get(f"/rest{path}", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok", f"{path} returned status={inner.get('status')}"
    if payload_key is not None:
        assert payload_key in inner, f"{path} missing payload key {payload_key!r}"


def test_stub_endpoints_authgated(tmp_path: Path) -> None:
    """Without creds, stubs still go through the auth dep (no anonymous endpoint exposure)."""
    response = _client(tmp_path).get("/rest/getPodcasts", params={"f": "json"})
    body = response.json()
    inner = body["subsonic-response"]
    assert inner["status"] == "failed"
    assert inner["error"]["code"] == 40
