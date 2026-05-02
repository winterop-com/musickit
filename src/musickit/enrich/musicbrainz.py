"""MusicBrainz release lookup.

Searches https://musicbrainz.org/ws/2/release/ for a release MBID matching the
album's tags (album title + album artist + track count when available). Honours
MB's 1 req/sec policy via the shared throttle in `_http`.
"""

from __future__ import annotations

import logging

import httpx

from musickit.enrich import EnrichmentResult
from musickit.enrich._http import get_client, throttled_get
from musickit.metadata import AlbumSummary, MusicBrainzIds, SourceTrack
from musickit.naming import is_various_artists

log = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
MB_HOST_KEY = "musicbrainz.org"


class MusicBrainzProvider:
    """Resolve a release MBID for an album using MusicBrainz."""

    name = "musicbrainz"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def enrich(self, album: AlbumSummary, tracks: list[SourceTrack]) -> EnrichmentResult:
        title = (album.album or "").strip()
        if not title:
            return EnrichmentResult(notes=["musicbrainz: no album title to query"])

        if album.is_compilation:
            artist_query = "Various Artists"
        else:
            artist_query = (album.album_artist or album.artist_fallback or "").strip()
            if is_various_artists(artist_query):
                artist_query = "Various Artists"

        client = self._client or get_client()
        try:
            mbid = self._search_release(client, title, artist_query, len(tracks))
        except httpx.HTTPError as exc:
            log.debug("musicbrainz lookup failed: %s", exc)
            return EnrichmentResult(notes=[f"musicbrainz: lookup failed ({exc!s})"])
        finally:
            if self._owns_client:
                client.close()

        if not mbid:
            return EnrichmentResult(notes=[f"musicbrainz: no release matched {title!r}"])

        return EnrichmentResult(musicbrainz=MusicBrainzIds(album_id=mbid))

    def _search_release(self, client: httpx.Client, title: str, artist: str, track_count: int) -> str | None:
        query_parts = [f'release:"{_escape(title)}"']
        if artist:
            query_parts.append(f'artist:"{_escape(artist)}"')
        if track_count:
            query_parts.append(f"tracks:{track_count}")
        query = " AND ".join(query_parts)

        response = throttled_get(
            client,
            f"{MB_BASE}/release/",
            host_key=MB_HOST_KEY,
            params={"query": query, "fmt": "json", "limit": 5},
        )
        response.raise_for_status()
        data = response.json()
        releases = data.get("releases") or []
        if not releases:
            return None
        # MB's `score` orders best matches first. Accept the first ≥ 90.
        best = releases[0]
        if int(best.get("score", 0)) < 90:
            return None
        return str(best["id"])


def _escape(value: str) -> str:
    """Escape Lucene metacharacters that MB's search index honours."""
    out: list[str] = []
    for ch in value:
        if ch in '+-&|!(){}[]^"~*?:\\/':
            out.append("\\")
        out.append(ch)
    return "".join(out)
