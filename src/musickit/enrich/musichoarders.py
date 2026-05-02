"""musichoarders.xyz cover provider — placeholder.

Per the site's integration policy at https://covers.musichoarders.xyz/ the
internal API is not condoned for automated use; integrations must "open the
official covers website in any compatible web view and let the user
interact with it." A future `musickit cover-pick` command will satisfy that by
opening `https://covers.musichoarders.xyz/?artist=...&album=...` in a browser
for manual selection. Keeping this module so the provider registry stays
stable.
"""

from __future__ import annotations

from urllib.parse import urlencode

from musickit.enrich import EnrichmentResult
from musickit.metadata import AlbumSummary, SourceTrack

MUSICHOARDERS_URL = "https://covers.musichoarders.xyz/"


def build_search_url(artist: str, album: str, *, resolution: int | None = None) -> str:
    """Build a musichoarders pre-fill URL for manual cover picking."""
    params: dict[str, str] = {}
    if artist:
        params["artist"] = artist
    if album:
        params["album"] = album
    if resolution:
        params["resolution"] = str(resolution)
    return f"{MUSICHOARDERS_URL}?{urlencode(params)}" if params else MUSICHOARDERS_URL


class MusicHoardersProvider:
    """No-op enrichment hook — manual flow lives in a future CLI command."""

    name = "musichoarders"

    def enrich(self, album: AlbumSummary, tracks: list[SourceTrack]) -> EnrichmentResult:
        return EnrichmentResult()
