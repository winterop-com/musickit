"""ScrobbleDispatcher: webhook + MQTT fan-out + endpoint integration + failure swallowing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.config import ScrobbleConfig, ScrobbleMqttConfig, ScrobbleWebhookConfig
from musickit.serve.ids import track_id
from musickit.serve.scrobble import ScrobbleDispatcher, ScrobbleEvent, _parse_broker

if TYPE_CHECKING:
    from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(submission: bool = True) -> ScrobbleEvent:
    return ScrobbleEvent(
        user="mort",
        track_id="tr_abc",
        title="Hello",
        artist="World",
        album="Greetings",
        duration_s=240.0,
        played_at="2026-05-06T18:00:00Z",
        submission=submission,
    )


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def test_webhook_posts_event_json() -> None:
    """Dispatcher posts a JSON body to the webhook URL on submission events."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://hook.example/play"))
    dispatcher = ScrobbleDispatcher(cfg, http=http)
    try:
        dispatcher.dispatch(_event())
    finally:
        dispatcher.shutdown()

    assert len(captured) == 1
    sent = captured[0]
    assert sent.method == "POST"
    assert sent.url == "http://hook.example/play"
    body = sent.read().decode()
    assert '"track_id":"tr_abc"' in body
    assert '"submission":true' in body


def test_webhook_includes_secret_header_when_configured() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://hook.example", secret="shh"))
    dispatcher = ScrobbleDispatcher(cfg, http=http)
    try:
        dispatcher.dispatch(_event())
    finally:
        dispatcher.shutdown()

    assert len(captured) == 1
    assert captured[0].headers.get("X-Musickit-Secret") == "shh"


def test_webhook_failure_does_not_raise() -> None:
    """Network errors must be swallowed — dispatcher is fire-and-forget."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bridge down")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://broken.example"))
    dispatcher = ScrobbleDispatcher(cfg, http=http)
    try:
        dispatcher.dispatch(_event())  # must not raise
    finally:
        dispatcher.shutdown()


def test_now_playing_filtered_by_default() -> None:
    """submission=False (`now playing` probe) is suppressed unless include_now_playing=True."""
    captured: list[httpx.Request] = []

    def _capture(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(_capture))
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://h.example"))
    dispatcher = ScrobbleDispatcher(cfg, http=http)
    try:
        dispatcher.dispatch(_event(submission=False))
    finally:
        dispatcher.shutdown()
    assert captured == []


def test_now_playing_forwarded_when_enabled() -> None:
    captured: list[httpx.Request] = []

    def _capture(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200)

    http = httpx.Client(transport=httpx.MockTransport(_capture))
    cfg = ScrobbleConfig(
        webhook=ScrobbleWebhookConfig(url="http://h.example"),
        include_now_playing=True,
    )
    dispatcher = ScrobbleDispatcher(cfg, http=http)
    try:
        dispatcher.dispatch(_event(submission=False))
    finally:
        dispatcher.shutdown()
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------


def test_mqtt_publishes_event_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dispatcher invokes paho-mqtt's publish() on the configured topic.

    Patches `_ensure_mqtt_client` to inject a MagicMock — clearer than
    fighting Python's import machinery to swap a real paho-mqtt install.
    """
    fake_client = MagicMock()

    def _inject(self: ScrobbleDispatcher, _cfg: ScrobbleMqttConfig) -> Any:
        with self._mqtt_lock:  # noqa: SLF001 — match real implementation's locking
            self._mqtt_client = fake_client  # noqa: SLF001
        return fake_client

    monkeypatch.setattr(ScrobbleDispatcher, "_ensure_mqtt_client", _inject)

    cfg = ScrobbleConfig(mqtt=ScrobbleMqttConfig(broker="mqtt://broker.example:1883", topic="t/play"))
    dispatcher = ScrobbleDispatcher(cfg)
    try:
        dispatcher.dispatch(_event())
    finally:
        dispatcher.shutdown()

    assert fake_client.publish.called
    args, kwargs = fake_client.publish.call_args
    assert args[0] == "t/play"
    payload = kwargs["payload"]
    assert '"track_id":"tr_abc"' in payload


def test_mqtt_disabled_when_ensure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `_ensure_mqtt_client` returns None (paho missing / connect failed), publish is skipped."""

    def _none(self: ScrobbleDispatcher, _cfg: ScrobbleMqttConfig) -> Any:
        del self
        return None

    monkeypatch.setattr(ScrobbleDispatcher, "_ensure_mqtt_client", _none)

    cfg = ScrobbleConfig(mqtt=ScrobbleMqttConfig(broker="mqtt://broker.example"))
    dispatcher = ScrobbleDispatcher(cfg)
    try:
        dispatcher.dispatch(_event())  # must not raise
    finally:
        dispatcher.shutdown()


# ---------------------------------------------------------------------------
# Broker URL parsing
# ---------------------------------------------------------------------------


def test_parse_broker_full_url() -> None:
    assert _parse_broker("mqtt://homeassistant.local:1883") == ("homeassistant.local", 1883)


def test_parse_broker_host_only() -> None:
    assert _parse_broker("homeassistant.local") == ("homeassistant.local", 1883)


def test_parse_broker_host_port_no_scheme() -> None:
    assert _parse_broker("hass:8883") == ("hass", 8883)


# ---------------------------------------------------------------------------
# /scrobble endpoint integration
# ---------------------------------------------------------------------------


def _client_with_track(
    tmp_path: Path,
    scrobble_cfg: ScrobbleConfig,
) -> tuple[TestClient, "FastAPI", str]:
    """Return (TestClient, app, track_id) — surfacing `app` so tests can
    poke `app.state.scrobble` without fighting `TestClient.app`'s ASGI typing.
    """
    cfg = ServeConfig(username="mort", password="secret", scrobble=scrobble_cfg)
    app = create_app(root=tmp_path, cfg=cfg)
    track = LibraryTrack(
        path=tmp_path / "Artist" / "Album" / "01.flac",
        title="Hello",
        artist="World",
        album="Greetings",
        duration_s=240.0,
    )
    album = LibraryAlbum(
        path=tmp_path / "Artist" / "Album",
        artist_dir="Artist",
        album_dir="Album",
        tag_album="Greetings",
        track_count=1,
        tracks=[track],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    return TestClient(app), app, track_id(track)


def test_endpoint_calls_dispatcher(tmp_path: Path) -> None:
    """`/scrobble?id=...&submission=true` resolves the track and invokes dispatcher.dispatch."""
    captured: list[ScrobbleEvent] = []
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://hook.example"))
    client, app, tid = _client_with_track(tmp_path, cfg)

    # Replace the dispatcher with a capturing one — easier than mocking
    # httpx through the live dispatcher and avoids racing with the
    # background pool.
    real_dispatch = app.state.scrobble.dispatch
    app.state.scrobble.dispatch = lambda event: captured.append(event)

    try:
        response = client.get("/rest/scrobble", params=_params(id=tid, submission="true"))
        assert response.status_code == 200
        body = response.json()
        assert body["subsonic-response"]["status"] == "ok"

        assert len(captured) == 1
        event = captured[0]
        assert event.track_id == tid
        assert event.title == "Hello"
        assert event.artist == "World"
        assert event.album == "Greetings"
        assert event.submission is True
    finally:
        app.state.scrobble.dispatch = real_dispatch
        app.state.scrobble.shutdown()


def test_endpoint_unknown_id_returns_ok_no_dispatch(tmp_path: Path) -> None:
    """Unknown track IDs still return ok (clients don't tolerate scrobble errors)."""
    cfg = ScrobbleConfig(webhook=ScrobbleWebhookConfig(url="http://hook.example"))
    client, app, _tid = _client_with_track(tmp_path, cfg)

    captured: list[ScrobbleEvent] = []
    app.state.scrobble.dispatch = lambda event: captured.append(event)

    try:
        response = client.get("/rest/scrobble", params=_params(id="tr_does_not_exist"))
        assert response.status_code == 200
        assert response.json()["subsonic-response"]["status"] == "ok"
        assert captured == []
    finally:
        app.state.scrobble.shutdown()


def test_endpoint_no_id_returns_ok(tmp_path: Path) -> None:
    """Missing id (some clients ping /scrobble with no id) — must still ack."""
    cfg = ScrobbleConfig()  # no forwarders configured
    client, app, _tid = _client_with_track(tmp_path, cfg)
    try:
        response = client.get("/rest/scrobble", params=_params())
        assert response.status_code == 200
        assert response.json()["subsonic-response"]["status"] == "ok"
    finally:
        app.state.scrobble.shutdown()


def test_endpoint_returns_ok_even_when_dispatcher_disabled(tmp_path: Path) -> None:
    """No webhook + no mqtt = no-op dispatcher; endpoint still returns ok."""
    client, app, tid = _client_with_track(tmp_path, ScrobbleConfig())
    try:
        response = client.get("/rest/scrobble", params=_params(id=tid, submission="true"))
        assert response.status_code == 200
        assert response.json()["subsonic-response"]["status"] == "ok"
    finally:
        app.state.scrobble.shutdown()
