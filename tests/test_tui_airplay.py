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
