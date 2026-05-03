"""mDNS / Bonjour advertisement — `_subsonic._tcp.local`.

Registers a Zeroconf service so LAN clients (Symfonium, Amperfy, the
musickit TUI itself) can discover the running serve without typing the
URL. The same service type Navidrome uses, so existing Subsonic clients
that auto-list servers pick it up too.

Tailscale users still need to type the tailnet URL once — mDNS doesn't
traverse the WireGuard tunnel.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from zeroconf import IPVersion, ServiceInfo, Zeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE = "_subsonic._tcp.local."


def register_service(
    *,
    port: int,
    instance_name: str | None = None,
) -> tuple[Zeroconf, ServiceInfo] | None:
    """Advertise the running serve over mDNS. Returns `(Zeroconf, ServiceInfo)` on success.

    Returns `None` if Zeroconf failed to start (no IPv4 multicast interface,
    permission denied, etc.) — callers should treat mDNS as best-effort.
    """
    hostname = socket.gethostname().split(".")[0] or "musickit"
    name = instance_name or f"musickit-{hostname}"
    full_name = f"{name}.{SERVICE_TYPE}"
    addresses = _local_ipv4_addresses()
    info = ServiceInfo(
        type_=SERVICE_TYPE,
        name=full_name,
        port=port,
        properties={
            "version": "1.16.1",
            "type": "musickit",
            "openSubsonic": "true",
        },
        server=f"{hostname}.local.",
        addresses=addresses or None,
    )
    try:
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info)
    except OSError as exc:  # pragma: no cover — network setup-dependent
        log.warning("mDNS registration failed: %s", exc)
        return None
    return zc, info


def unregister_service(zc: Zeroconf, info: ServiceInfo) -> None:
    """Deregister + close. Safe to call even if registration partially failed."""
    try:
        zc.unregister_service(info)
    except Exception:  # pragma: no cover — best effort
        pass
    try:
        zc.close()
    except Exception:  # pragma: no cover
        pass


def _local_ipv4_addresses() -> list[bytes]:
    """Pick the IPv4 address(es) we should announce.

    Best-effort UDP-connect trick to learn which interface routes to public
    Internet — that's the one we want to advertise on the LAN. Falls back to
    127.0.0.1 if the trick fails (no network, headless boot, etc.).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip: Any = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    try:
        return [socket.inet_aton(str(ip))]
    except OSError:
        return [socket.inet_aton("127.0.0.1")]
