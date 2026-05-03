"""mDNS register / unregister smoke + browse listener mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

from zeroconf import ServiceInfo

from musickit.serve.discovery import register_service, unregister_service
from musickit.tui.discovery import _Listener


def test_register_then_unregister_smoke() -> None:
    """register_service starts Zeroconf and unregister_service tears it down without raising."""
    handle = register_service(port=14533, instance_name="musickit-test-smoke")
    if handle is None:
        # CI environments without IPv4 multicast still pass the test by skipping.
        import pytest

        pytest.skip("Zeroconf could not start (likely no multicast iface in this environment)")
    zc, info = handle
    try:
        assert info.name.startswith("musickit-test-smoke.")
        assert info.port == 14533
        # `properties` round-trips as bytes — flatten + decode for the check.
        props = {k.decode(): v.decode() for k, v in info.properties.items() if isinstance(v, bytes)}
        assert props["type"] == "musickit"
        assert props["openSubsonic"] == "true"
    finally:
        unregister_service(zc, info)


def test_browse_listener_dedupes_by_service_name() -> None:
    """Listener maps Add events to DiscoveredServer; Remove drops them; Add again doesn't dupe."""
    listener = _Listener()

    fake_zc = MagicMock()
    fake_info = ServiceInfo(
        type_="_subsonic._tcp.local.",
        name="musickit-foo._subsonic._tcp.local.",
        port=4533,
        addresses=[bytes([192, 168, 1, 10])],
        server="foo.local.",
    )
    fake_zc.get_service_info.return_value = fake_info

    listener.add_service(fake_zc, "_subsonic._tcp.local.", "musickit-foo._subsonic._tcp.local.")
    listener.add_service(fake_zc, "_subsonic._tcp.local.", "musickit-foo._subsonic._tcp.local.")

    assert len(listener.servers) == 1
    server = next(iter(listener.servers.values()))
    assert server.name == "musickit-foo"
    assert server.host == "192.168.1.10"
    assert server.port == 4533
    assert server.url == "http://192.168.1.10:4533"
    assert server.is_musickit is True

    listener.remove_service(fake_zc, "_subsonic._tcp.local.", "musickit-foo._subsonic._tcp.local.")
    assert listener.servers == {}


def test_browse_listener_skips_unresolvable_entries() -> None:
    """get_service_info returning None / empty addresses must not crash or add an entry."""
    listener = _Listener()
    fake_zc = MagicMock()

    fake_zc.get_service_info.return_value = None
    listener.add_service(fake_zc, "_subsonic._tcp.local.", "ghost._subsonic._tcp.local.")
    assert listener.servers == {}

    info_no_addr = ServiceInfo(
        type_="_subsonic._tcp.local.",
        name="addrless._subsonic._tcp.local.",
        port=4533,
        addresses=[],
        server="",
    )
    fake_zc.get_service_info.return_value = info_no_addr
    listener.add_service(fake_zc, "_subsonic._tcp.local.", "addrless._subsonic._tcp.local.")
    assert listener.servers == {}


def test_browse_listener_recognises_non_musickit_servers() -> None:
    """Navidrome / real Subsonic advertise `_subsonic._tcp` too; we list them as 'other'."""
    listener = _Listener()
    fake_zc = MagicMock()
    fake_zc.get_service_info.return_value = ServiceInfo(
        type_="_subsonic._tcp.local.",
        name="navidrome._subsonic._tcp.local.",
        port=4533,
        addresses=[bytes([10, 0, 0, 5])],
        server="navi.local.",
    )
    listener.add_service(fake_zc, "_subsonic._tcp.local.", "navidrome._subsonic._tcp.local.")
    server = next(iter(listener.servers.values()))
    assert server.name == "navidrome"
    assert server.is_musickit is False
