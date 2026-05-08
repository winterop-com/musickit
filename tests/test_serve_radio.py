"""Subsonic internet-radio endpoints — backed by `radio.load_stations()`."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit import radio
from musickit.radio import DEFAULT_STATIONS
from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    # Point stations_path() at a tmp file so the test doesn't depend on
    # the developer's `~/.config/musickit/radio.toml`.
    monkeypatch.setattr(radio, "stations_path", lambda: tmp_path / "radio.toml")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def test_get_internet_radio_stations_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no user radio.toml the endpoint returns the baked-in defaults."""
    body = _client(tmp_path, monkeypatch).get("/rest/getInternetRadioStations", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    stations = inner["internetRadioStations"]["internetRadioStation"]
    assert len(stations) == len(DEFAULT_STATIONS)
    # Every station has the spec-required fields.
    for s in stations:
        assert s["id"]
        assert s["name"]
        assert s["streamUrl"]


def test_get_internet_radio_stations_user_entries_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User TOML entries appear ahead of defaults, mirroring `load_stations`."""
    (tmp_path / "radio.toml").write_text(
        '[[stations]]\nname = "User One"\nurl = "https://user.example/stream"\nhomepage = "https://user.example/"\n',
        encoding="utf-8",
    )
    body = _client(tmp_path, monkeypatch).get("/rest/getInternetRadioStations", params=_params()).json()
    stations = body["subsonic-response"]["internetRadioStations"]["internetRadioStation"]
    assert stations[0]["name"] == "User One"
    assert stations[0]["streamUrl"] == "https://user.example/stream"
    assert stations[0]["homepageUrl"] == "https://user.example/"
    # Defaults follow.
    assert len(stations) == len(DEFAULT_STATIONS) + 1


@pytest.mark.parametrize(
    "path",
    [
        "/createInternetRadioStation",
        "/updateInternetRadioStation",
        "/deleteInternetRadioStation",
    ],
)
def test_radio_write_endpoints_are_success_noops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """Write endpoints return ok — stations live in radio.toml, not the API."""
    body = _client(tmp_path, monkeypatch).get(f"/rest{path}", params=_params()).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_get_internet_radio_stations_authgated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without creds the endpoint goes through the auth dep."""
    monkeypatch.setattr(radio, "stations_path", lambda: tmp_path / "radio.toml")
    cfg = ServeConfig(username="mort", password="secret")
    body = (
        TestClient(create_app(root=tmp_path, cfg=cfg))
        .get("/rest/getInternetRadioStations", params={"f": "json"})
        .json()
    )
    inner = body["subsonic-response"]
    assert inner["status"] == "failed"
    assert inner["error"]["code"] == 40
