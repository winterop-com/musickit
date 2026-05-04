"""AirPlay discovery + controller smoke tests (pyatv mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from musickit.tui.airplay import AirPlayController, AirPlayDevice, discover_airplay_devices


class _FakeService:
    def __init__(self, protocol: object) -> None:
        self.protocol = protocol


class _FakeConfig:
    def __init__(self, name: str, address: str, identifier: str, protocols: list[object]) -> None:
        self.name = name
        self.address = address
        self.identifier = identifier
        self.services = [_FakeService(p) for p in protocols]


@pytest.mark.asyncio
async def test_discover_filters_to_audio_capable_devices() -> None:
    """Only configs that expose RAOP or AirPlay should be returned."""
    from pyatv.const import Protocol

    audio_cfg = _FakeConfig("Living Room", "192.168.1.50", "id-1", [Protocol.RAOP, Protocol.AirPlay])
    video_only_cfg = _FakeConfig("Old AppleTV", "192.168.1.51", "id-2", [Protocol.MRP])

    with patch("musickit.tui.airplay.pyatv.scan", new_callable=AsyncMock) as scan_mock:
        scan_mock.return_value = [audio_cfg, video_only_cfg]
        devices = await discover_airplay_devices(timeout=0.1)

    assert [d.name for d in devices] == ["Living Room"]
    assert devices[0].address == "192.168.1.50"


@pytest.mark.asyncio
async def test_discover_returns_empty_when_no_devices() -> None:
    with patch("musickit.tui.airplay.pyatv.scan", new_callable=AsyncMock) as scan_mock:
        scan_mock.return_value = []
        assert await discover_airplay_devices(timeout=0.1) == []


def test_airplay_device_display_label() -> None:
    cfg = _FakeConfig("HomePod", "10.0.0.5", "abc", [])
    dev = AirPlayDevice(name="HomePod", address="10.0.0.5", identifier="abc", config=cfg)  # type: ignore[arg-type]
    assert dev.display_label == "HomePod (10.0.0.5)"


def test_controller_play_url_no_op_without_connected_device() -> None:
    """Calling play_url before connect() should be a silent no-op."""
    controller = AirPlayController()
    try:
        controller.play_url("http://example/stream")  # must not raise
    finally:
        controller.disconnect()


def test_controller_disconnect_is_idempotent() -> None:
    """Disconnecting twice is safe — used during app shutdown."""
    controller = AirPlayController()
    controller.disconnect()
    controller.disconnect()  # second call after loop already stopped


def test_controller_routes_play_url_to_pyatv() -> None:
    """When a device is connected, play_url forwards to atv.stream.play_url."""
    from pyatv.const import Protocol

    cfg = _FakeConfig("HomePod", "10.0.0.5", "hp", [Protocol.RAOP])
    fake_atv = MagicMock()
    fake_atv.stream.play_url = AsyncMock()

    controller = AirPlayController()
    try:
        with patch("musickit.tui.airplay.pyatv.connect", new_callable=AsyncMock) as connect_mock:
            connect_mock.return_value = fake_atv
            controller.connect(AirPlayDevice(name="HomePod", address="10.0.0.5", identifier="hp", config=cfg))  # type: ignore[arg-type]
            assert controller.device is not None
            controller.play_url("http://example/stream.m3u")
        fake_atv.stream.play_url.assert_called_once_with("http://example/stream.m3u")
    finally:
        controller.disconnect()


def test_get_or_create_airplay_does_not_stop_player() -> None:
    """Opening the AirPlay picker (which lazy-creates the controller) must
    not call `player.set_airplay` — that triggers `stop()` and would
    interrupt current playback the moment the picker is opened.
    Wiring the controller into the player is `switch_airplay`'s job,
    once the user actually picks a device.
    """
    from musickit.tui.app import MusickitApp

    app = MusickitApp(root=None)
    set_airplay_calls: list[object] = []
    original = app._player.set_airplay

    def spy(controller: object) -> None:
        set_airplay_calls.append(controller)
        original(controller)  # type: ignore[arg-type]

    app._player.set_airplay = spy  # type: ignore[method-assign]
    try:
        controller = app.get_or_create_airplay()
        assert controller is not None
        assert set_airplay_calls == [], "get_or_create_airplay must not wire the controller into the player"
    finally:
        if app._airplay is not None:
            app._airplay.disconnect()


def test_player_toggle_pause_routes_to_airplay() -> None:
    """`toggle_pause` while AirPlay is the active output sends pause/resume
    to pyatv. Regression: it only flipped the local `_paused` flag, so the
    remote device kept playing while the UI showed paused.
    """
    from musickit.tui.player import AudioPlayer

    fake_controller = MagicMock()
    fake_controller.device = MagicMock()
    fake_controller.play_url = MagicMock()
    fake_controller.pause = MagicMock()
    fake_controller.resume = MagicMock()

    player = AudioPlayer(airplay=fake_controller)
    try:
        player.play("http://example/stream.m3u")
        # First toggle: paused → must call airplay.pause().
        player.toggle_pause()
        fake_controller.pause.assert_called_once()
        fake_controller.resume.assert_not_called()
        # Second toggle: resumed → must call airplay.resume().
        player.toggle_pause()
        fake_controller.resume.assert_called_once()
    finally:
        player.stop()


def test_player_set_volume_routes_to_airplay() -> None:
    """`set_volume` while AirPlay is the active output forwards the level to
    the device. Regression: only `_volume` (local software gain) changed,
    which has no effect when audio is decoded on the remote device.
    """
    from musickit.tui.player import AudioPlayer

    fake_controller = MagicMock()
    fake_controller.device = MagicMock()
    fake_controller.play_url = MagicMock()
    fake_controller.set_volume = MagicMock()

    player = AudioPlayer(airplay=fake_controller)
    try:
        player.play("http://example/stream.m3u")
        player.set_volume(40)
        fake_controller.set_volume.assert_called_once_with(40)
    finally:
        player.stop()


def test_player_local_playback_does_not_route_to_airplay() -> None:
    """Pause / volume on a LOCAL playback (no AirPlay device) must not call
    pyatv even when an AirPlayController is attached but no device picked.
    """
    from musickit.tui.player import AudioPlayer

    fake_controller = MagicMock()
    fake_controller.device = None  # controller present, no device picked
    fake_controller.pause = MagicMock()
    fake_controller.resume = MagicMock()
    fake_controller.set_volume = MagicMock()

    player = AudioPlayer(airplay=fake_controller)
    try:
        # Without a device, play() falls through to local decode — but we
        # don't actually need to start anything. Just confirm the routing
        # gate.
        player.toggle_pause()
        player.set_volume(50)
        fake_controller.pause.assert_not_called()
        fake_controller.resume.assert_not_called()
        fake_controller.set_volume.assert_not_called()
    finally:
        player.stop()


def test_player_airplay_path_reports_playing_after_play_url() -> None:
    """After `play(url)` while AirPlay is connected, `is_playing` must be True.
    Regression: `_teardown_playback` set `_stopped = True` and the AirPlay
    branch never flipped it back, so `is_playing` returned False even
    while the AirPlay device was streaming.
    """
    from musickit.tui.player import AudioPlayer

    fake_controller = MagicMock()
    fake_controller.device = MagicMock()  # truthy = "connected"
    fake_controller.play_url = MagicMock()

    player = AudioPlayer(airplay=fake_controller)
    try:
        player.play("http://example/stream.m3u")
        fake_controller.play_url.assert_called_once_with("http://example/stream.m3u")
        assert player.is_playing
        assert not player.is_paused
    finally:
        player.stop()


def test_controller_detach_keeps_loop_alive_for_reuse() -> None:
    """detach() resets the device but the controller's loop stays usable."""
    from pyatv.const import Protocol

    cfg = _FakeConfig("HomePod", "10.0.0.5", "hp", [Protocol.RAOP])
    fake_atv = MagicMock()

    controller = AirPlayController()
    try:
        with patch("musickit.tui.airplay.pyatv.connect", new_callable=AsyncMock) as connect_mock:
            connect_mock.return_value = fake_atv
            controller.connect(AirPlayDevice(name="HomePod", address="10.0.0.5", identifier="hp", config=cfg))  # type: ignore[arg-type]
            assert controller.device is not None
            controller.detach()
            assert controller.device is None
            # Loop is still running — discover() works after detach.
            with patch("musickit.tui.airplay.pyatv.scan", new_callable=AsyncMock) as scan_mock:
                scan_mock.return_value = []
                assert controller.discover(timeout=0.1) == []
    finally:
        controller.disconnect()
