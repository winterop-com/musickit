"""LRCLIB fetcher — `https://lrclib.net/api/get`. No API key required."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://lrclib.net/api/get"
_DEFAULT_TIMEOUT = 10.0


class LrcLibError(Exception):
    """Raised on non-404 HTTP failure or unparseable response from LRCLIB."""


class LrcLibClient:
    """Tiny LRCLIB client. One method: `get(...)`. 404 returns None, not an error."""

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        user_agent: str = "musickit/0.9.0 (+https://github.com/winterop-com/musickit)",
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self._owns_http = http is None
        self.http = http or httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})

    def get(
        self,
        *,
        track_name: str,
        artist_name: str,
        album_name: str | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any] | None:
        """Fetch lyrics for `(artist, album, track, duration)`.

        Returns the raw LRCLIB JSON dict on hit (callers pick `syncedLyrics`
        or `plainLyrics`), `None` on 404, or raises `LrcLibError` on any
        other failure. Duration is optional but improves match quality.
        """
        if not track_name or not artist_name:
            return None
        params: dict[str, str | int] = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
        if album_name:
            params["album_name"] = album_name
        if duration_s is not None and duration_s > 0:
            params["duration"] = int(round(duration_s))
        try:
            resp = self.http.get(self.base_url, params=params)
        except httpx.HTTPError as exc:
            raise LrcLibError(f"network error: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise LrcLibError(f"HTTP {resp.status_code}: {resp.text[:120]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LrcLibError(f"non-JSON body: {resp.text[:120]}") from exc
        if not isinstance(data, dict):
            raise LrcLibError(f"unexpected response shape: {type(data).__name__}")
        return data

    def best_lyrics(self, payload: dict[str, Any]) -> str | None:
        """Pick the best lyrics body from an LRCLIB response. Synced wins; empty → None."""
        synced = payload.get("syncedLyrics")
        if isinstance(synced, str) and synced.strip():
            return synced
        plain = payload.get("plainLyrics")
        if isinstance(plain, str) and plain.strip():
            return plain
        return None

    def close(self) -> None:
        """Close the owned httpx client (no-op if one was injected)."""
        if self._owns_http:
            try:
                self.http.close()
            except Exception:  # pragma: no cover — best-effort on shutdown
                pass

    def __enter__(self) -> LrcLibClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
