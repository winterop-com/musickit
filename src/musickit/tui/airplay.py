"""AirPlay output via pyatv — route TUI playback to a HomePod / AirPort Express / etc.

pyatv handles AirPlay 1 (RAOP) and AirPlay 2 (FairPlay-encrypted) speakers
in one API. We use the `play_url` mode: hand the device an HTTP URL and it
fetches + decodes itself. That works for:

  - radio stream URLs (just pass through)
  - musickit serve `/rest/stream` URLs (when in TUI Subsonic-client mode,
    `LibraryTrack.stream_url` already carries auth)

Local-file mode (`musickit tui ./output`) doesn't have a URL to hand off
without spinning up a tiny local HTTP server — deferred to v2.

pyatv is fully async; we run a dedicated asyncio loop on a background
thread so the synchronous TUI / AudioPlayer code can call into it via
`asyncio.run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pyatv
from pyatv.const import Protocol

if TYPE_CHECKING:
    from pyatv.interface import AppleTV, BaseConfig

log = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AirPlayDevice:
    """One discovered AirPlay-capable device."""

    name: str
    address: str
    identifier: str
    # Carry the raw config so the controller can connect without re-scanning.
    config: BaseConfig

    @property
    def display_label(self) -> str:
        return f"{self.name} ({self.address})"


async def discover_airplay_devices(*, timeout: float = DISCOVERY_TIMEOUT_S) -> list[AirPlayDevice]:
    """Scan the LAN for AirPlay-capable speakers / receivers."""
    loop = asyncio.get_running_loop()
    configs = await pyatv.scan(loop, timeout=int(max(1, timeout)), protocol={Protocol.RAOP, Protocol.AirPlay})
    devices: list[AirPlayDevice] = []
    for cfg in configs:
        # Only keep configs that actually expose an audio service we can stream to.
        services = {svc.protocol for svc in cfg.services}
        if not (services & {Protocol.RAOP, Protocol.AirPlay}):
            continue
        devices.append(
            AirPlayDevice(
                name=cfg.name,
                address=str(cfg.address),
                identifier=cfg.identifier or "",
                config=cfg,
            )
        )
    return devices


class AirPlayController:
    """Synchronous facade over pyatv's async API.

    Owns a background asyncio loop. Public methods (`connect`, `play_url`,
    `stop`, `set_volume`, `disconnect`) are synchronous and dispatch into
    the loop via `run_coroutine_threadsafe`. The TUI / AudioPlayer call
    into this with normal blocking methods.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True, name="musickit-airplay")
        self._loop_thread.start()
        self._atv: AppleTV | None = None
        self._device: AirPlayDevice | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def device(self) -> AirPlayDevice | None:
        """Currently connected device, or None."""
        return self._device

    def discover(self, *, timeout: float = DISCOVERY_TIMEOUT_S) -> list[AirPlayDevice]:
        """Blocking discovery — runs the async scan on the controller's loop."""
        future = asyncio.run_coroutine_threadsafe(
            discover_airplay_devices(timeout=timeout),
            self._loop,
        )
        return future.result(timeout=timeout + 2.0)

    def connect(self, device: AirPlayDevice) -> None:
        """Establish a session with `device`. Disconnects any prior connection."""
        future = asyncio.run_coroutine_threadsafe(self._connect(device), self._loop)
        future.result(timeout=10.0)

    async def _connect(self, device: AirPlayDevice) -> None:
        if self._atv is not None:
            await self._disconnect()
        self._atv = await pyatv.connect(device.config, self._loop)
        self._device = device
        log.info("airplay: connected to %s", device.display_label)

    def play_url(self, url: str) -> None:
        """Tell the connected device to play `url`. No-op if no device."""
        if self._atv is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._play_url(url), self._loop)
        future.result(timeout=15.0)

    async def _play_url(self, url: str) -> None:
        assert self._atv is not None
        await self._atv.stream.play_url(url)

    def stop(self) -> None:
        """Stop playback on the device."""
        if self._atv is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._stop(), self._loop)
        try:
            future.result(timeout=5.0)
        except Exception:  # pragma: no cover — best-effort
            pass

    async def _stop(self) -> None:
        assert self._atv is not None
        # `stream.play_url` doesn't expose a stop; fall back to remote control.
        # Some devices reject stop-while-idle; swallow.
        try:
            await self._atv.remote_control.stop()
        except Exception:  # pragma: no cover
            pass

    def disconnect(self) -> None:
        """Tear down the AirPlay session and stop the background loop."""
        if self._atv is not None:
            future = asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
            try:
                future.result(timeout=5.0)
            except Exception:  # pragma: no cover
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=2.0)

    async def _disconnect(self) -> None:
        if self._atv is None:
            return
        try:
            self._atv.close()
        except Exception:  # pragma: no cover
            pass
        self._atv = None
        self._device = None
