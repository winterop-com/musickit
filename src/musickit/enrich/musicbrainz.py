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
            ids = self._search_release(client, title, artist_query, len(tracks))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Catch a wider net than just transport errors: malformed JSON,
            # unexpected response shapes (HTML error pages, missing keys),
            # and surprise types in `score`/`id` would otherwise crash the
            # entire enrichment pass instead of yielding a per-album warning.
            log.debug("musicbrainz lookup failed: %s", exc)
            return EnrichmentResult(notes=[f"musicbrainz: lookup failed ({exc!s})"])
        finally:
            if self._owns_client:
                client.close()

        if ids is None:
            return EnrichmentResult(notes=[f"musicbrainz: no release matched {title!r}"])

        return EnrichmentResult(musicbrainz=ids)

    def _search_release(self, client: httpx.Client, title: str, artist: str, track_count: int) -> MusicBrainzIds | None:
        """Return the best-match release with album/artist/release-group MBIDs filled in.

        MB's release search response includes `artist-credit` and
        `release-group` inline by default, so we can populate three of the
        four MB ID fields in a single round trip. Per-track recording MBIDs
        require a follow-up `release/<mbid>?inc=recordings` lookup; that's
        a future addition.
        """
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

        album_id = str(best["id"])
        # `artist-credit` is a list of credit objects; we want the primary
        # (first) artist. Older MB responses occasionally put the artist at
        # `artist-credit[0]["name"]` only — guard for the missing key.
        artist_id: str | None = None
        credits = best.get("artist-credit") or []
        if credits:
            primary_artist = credits[0].get("artist") if isinstance(credits[0], dict) else None
            if isinstance(primary_artist, dict) and primary_artist.get("id"):
                artist_id = str(primary_artist["id"])
        # `release-group` is a flat dict in the search response.
        release_group_id: str | None = None
        rg = best.get("release-group")
        if isinstance(rg, dict) and rg.get("id"):
            release_group_id = str(rg["id"])

        return MusicBrainzIds(
            album_id=album_id,
            artist_id=artist_id,
            release_group_id=release_group_id,
        )


def lookup_release_year(
    album: str,
    artist: str,
    *,
    client: httpx.Client | None = None,
) -> str | None:
    """Return the 4-digit release year for `(album, artist)` per MusicBrainz, or None.

    Used by `library --fix` to backfill a missing year tag without a full
    enrichment pass. Honours the same 1 req/sec throttle as `enrich`.
    """
    if not album.strip():
        return None
    own_client = client is None
    client = client or get_client()
    try:
        query_parts = [f'release:"{_escape(album)}"']
        if artist:
            query_parts.append(f'artist:"{_escape(artist)}"')
        response = throttled_get(
            client,
            f"{MB_BASE}/release/",
            host_key=MB_HOST_KEY,
            params={"query": " AND ".join(query_parts), "fmt": "json", "limit": 5},
        )
        response.raise_for_status()
        releases = response.json().get("releases") or []
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        # Same defensive net as `MusicBrainzProvider.enrich`: any malformed
        # MB response should yield None (skip the fix), not crash the run.
        log.debug("musicbrainz year lookup failed: %s", exc)
        return None
    finally:
        if own_client:
            client.close()
    for release in releases:
        try:
            score = int(release.get("score", 0))
        except (TypeError, ValueError):
            continue
        if score < 90:
            continue
        date = str(release.get("date") or "").strip()
        if len(date) >= 4 and date[:4].isdigit():
            return date[:4]
    return None


def _escape(value: str) -> str:
    """Escape Lucene metacharacters that MB's search index honours."""
    out: list[str] = []
    for ch in value:
        if ch in '+-&|!(){}[]^"~*?:\\/':
            out.append("\\")
        out.append(ch)
    return "".join(out)
