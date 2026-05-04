"""Shared httpx client + 1 req/sec throttle for online enrichment providers."""

from __future__ import annotations

import socket
import threading
import time

import httpx

from musickit import __version__

USER_AGENT = f"musickit/{__version__} ( https://github.com/winterop-com/musickit )"
DEFAULT_TIMEOUT = 15.0
RATE_LIMIT_SECONDS = 1.0  # MusicBrainz allows 1 req/sec for anonymous use.


class _Throttle:
    """Thread-safe minimum-interval gate between requests to one host."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


_throttles: dict[str, _Throttle] = {}
_throttle_lock = threading.Lock()


def get_client() -> httpx.Client:
    """Build an httpx client with our polite defaults. Caller closes it."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )


def is_online(timeout: float = 0.5) -> bool:
    """Return True when MusicBrainz is reachable (TCP-level check, no HTTP).

    Short timeout (500 ms) — a real handshake completes in well under 100 ms;
    blocking longer just adds latency to offline runs. On flaky networks where
    the probe is unreliable, pass `--enrich` to bypass it entirely.
    """
    try:
        with socket.create_connection(("musicbrainz.org", 443), timeout=timeout):
            return True
    except OSError:
        return False


def throttled_get(client: httpx.Client, url: str, *, host_key: str, **kwargs: object) -> httpx.Response:
    """GET `url` with a host-keyed minimum-interval throttle applied first."""
    with _throttle_lock:
        throttle = _throttles.setdefault(host_key, _Throttle(RATE_LIMIT_SECONDS))
    throttle.wait()
    return client.get(url, **kwargs)  # type: ignore[arg-type]
