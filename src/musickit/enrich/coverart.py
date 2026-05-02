"""Cover Art Archive front-cover fetcher (https://coverartarchive.org).

Given a release MBID resolved by `MusicBrainzProvider`, fetches the front
image. Returns it as a `CoverCandidate(source=ONLINE)` so the cover-picker
can compare it against any local art.
"""

from __future__ import annotations

import io
import logging

import httpx

from musickit.cover import CoverCandidate, CoverSource
from musickit.enrich import EnrichmentResult
from musickit.enrich._http import get_client, throttled_get
from musickit.metadata import AlbumSummary, SourceTrack

log = logging.getLogger(__name__)

CAA_BASE = "https://coverartarchive.org"
CAA_HOST_KEY = "coverartarchive.org"


class CoverArtArchiveProvider:
    """Fetch the front cover from the Cover Art Archive."""

    name = "coverart"

    def __init__(self, client: httpx.Client | None = None, *, max_edge: int = 1200) -> None:
        self._client = client
        self._owns_client = client is None
        self._max_edge = max_edge

    def enrich(self, album: AlbumSummary, tracks: list[SourceTrack]) -> EnrichmentResult:
        # CAA needs a release MBID — we never resolve one ourselves, so we'd
        # have to be called as a follow-up to MusicBrainz. The orchestrator
        # in `musickit.enrich.run_enrichment` handles that hand-off. When called
        # standalone there's no MBID to act on.
        return EnrichmentResult()

    def fetch(self, release_mbid: str) -> EnrichmentResult:
        """Fetch the front cover for `release_mbid`. Returns empty on any failure.

        CAA 302-redirects to archive.org's CDN, which periodically returns 500s
        on individual asset requests. None of these are fatal — we silently
        fall back to local cover candidates and surface the failure as a note
        on the album report.
        """
        client = self._client or get_client()
        try:
            try:
                response = throttled_get(
                    client,
                    f"{CAA_BASE}/release/{release_mbid}/front-{self._max_edge}",
                    host_key=CAA_HOST_KEY,
                )
            except httpx.HTTPError as exc:
                log.debug("CAA fetch failed for %s: %s", release_mbid, exc)
                return EnrichmentResult(notes=[f"coverart: fetch failed ({exc!s})"])

            if response.status_code == 404:
                return EnrichmentResult(notes=[f"coverart: no art for release {release_mbid}"])
            if response.status_code >= 400:
                # 5xx from the IA CDN, 401/403 from CAA proper, etc.
                log.debug("CAA returned HTTP %s for %s", response.status_code, release_mbid)
                return EnrichmentResult(notes=[f"coverart: HTTP {response.status_code} for release {release_mbid}"])

            data = response.content
            mime = response.headers.get("content-type", "image/jpeg").split(";", 1)[0].strip()
            width, height = _measure(data)
            if width == 0 or height == 0:
                return EnrichmentResult(notes=[f"coverart: image bytes for {release_mbid} weren't decodable"])
            cover = CoverCandidate(
                source=CoverSource.ONLINE,
                data=data,
                mime=mime,
                width=width,
                height=height,
                label=f"coverartarchive.org/{release_mbid}",
            )
            return EnrichmentResult(extra_covers=[cover])
        finally:
            if self._owns_client:
                client.close()


def _measure(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return image.size
    except Exception:
        return (0, 0)
