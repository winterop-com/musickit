"""Web UI: /web/radio fragment + sidebar Radio entry."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit import radio
from musickit.library.models import LibraryIndex
from musickit.radio import DEFAULT_STATIONS
from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[]))  # noqa: SLF001
    return TestClient(app)


def _login(client: TestClient) -> None:
    """Helper: log in via the existing CSRF-aware flow."""
    import re

    form = client.get("/login")
    match = re.search(r'name="csrf"\s+value="([^"]+)"', form.text)
    assert match
    response = client.post(
        "/login",
        data={"username": "mort", "password": "secret", "csrf": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_radio_fragment_redirects_to_login_when_anonymous(tmp_path: Path) -> None:
    """`/web/radio` is auth-gated like the other fragment endpoints."""
    response = _client(tmp_path).get("/web/radio", follow_redirects=False)
    assert response.status_code == 303


def test_radio_fragment_lists_default_stations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logged-in fragment renders one row-button per station."""
    monkeypatch.setattr(radio, "stations_path", lambda: tmp_path / "radio.toml")
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/radio")
    assert response.status_code == 200
    html = response.text
    # Every default station's name should appear in the rendered fragment.
    for station in DEFAULT_STATIONS:
        assert station.name in html, f"{station.name!r} missing from radio fragment"
    # Each row carries a play-radio action with the stream URL.
    assert 'data-action="play-radio"' in html
    assert DEFAULT_STATIONS[0].url in html


def test_radio_fragment_includes_user_stations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User entries from radio.toml are rendered alongside defaults."""
    monkeypatch.setattr(radio, "stations_path", lambda: tmp_path / "radio.toml")
    (tmp_path / "radio.toml").write_text(
        '[[stations]]\nname = "User Beach FM"\nurl = "https://user.example/beach"\n',
        encoding="utf-8",
    )
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/radio")
    assert "User Beach FM" in response.text
    assert "https://user.example/beach" in response.text


def test_shell_renders_radio_panel(tmp_path: Path) -> None:
    """The sidebar Radio panel is part of the main shell."""
    client = _client(tmp_path)
    _login(client)
    html = client.get("/web").text
    assert 'data-action="load-radio"' in html
    assert "Radio" in html
