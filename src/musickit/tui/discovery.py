"""Browse `_subsonic._tcp.local` for musickit servers on the LAN."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

log = logging.getLogger(__name__)

_SERVICE_TYPE = "_subsonic._tcp.local."


@dataclass(frozen=True)
class DiscoveredServer:
    """One mDNS-discovered Subsonic-compatible server."""

    name: str  # pretty (instance) name, e.g. `musickit-mlaptop`
    host: str  # IP address or hostname
    port: int
    url: str

    @property
    def is_musickit(self) -> bool:
        """True when the advertisement's `type` TXT record says it's a musickit serve."""
        return self.name.startswith("musickit")


class _Listener(ServiceListener):
    """Capture every Add into a deduped list keyed by service name."""

    def __init__(self) -> None:
        self.servers: dict[str, DiscoveredServer] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:  # noqa: D102
        info = zc.get_service_info(type_, name, timeout=2000)
        if info is None:
            return
        addresses = [".".join(str(b) for b in addr) for addr in info.addresses if len(addr) == 4]
        host = addresses[0] if addresses else (info.server or "").rstrip(".")
        if not host:
            return
        pretty = name.split(f".{_SERVICE_TYPE}")[0]
        self.servers[name] = DiscoveredServer(
            name=pretty,
            host=host,
            port=info.port or 0,
            url=f"http://{host}:{info.port}",
        )

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:  # noqa: D102
        self.servers.pop(name, None)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:  # noqa: D102
        self.add_service(zc, type_, name)


def browse_subsonic_servers(*, timeout: float = 1.5) -> list[DiscoveredServer]:
    """Browse `_subsonic._tcp.local` for `timeout` seconds. Returns deduped list."""
    listener = _Listener()
    zc: Zeroconf | None = None
    try:
        zc = Zeroconf()
    except OSError as exc:  # pragma: no cover — no multicast iface available
        log.warning("mDNS browse: failed to start Zeroconf: %s", exc)
        return []
    try:
        ServiceBrowser(zc, _SERVICE_TYPE, listener)
        time.sleep(timeout)
    finally:
        try:
            zc.close()
        except Exception:  # pragma: no cover
            pass
    return list(listener.servers.values())
