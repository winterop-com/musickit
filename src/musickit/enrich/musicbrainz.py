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
        notes: list[str] = []
        try:
            ids = self._search_release(client, title, artist_query, len(tracks))
            if ids is not None and ids.album_id and tracks:
                # Follow-up: per-track recording MBIDs aren't in the search
                # response. Fetch the release with `inc=recordings`, map by
                # (disc, position) to our SourceTrack instances. Best-effort
                # — failure here just leaves track.mb_recording_id unset and
                # the album-level IDs are still written. Skipped when we
                # have no tracks to map (e.g. dry-run / metadata probe).
                try:
                    self._apply_recording_ids(client, ids.album_id, tracks)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    log.debug("musicbrainz recordings lookup failed: %s", exc)
                    notes.append(f"musicbrainz: per-track lookup failed ({exc!s})")
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
            return EnrichmentResult(notes=[f"musicbrainz: no release matched {title!r}", *notes])

        return EnrichmentResult(musicbrainz=ids, notes=notes)

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

    def _apply_recording_ids(self, client: httpx.Client, release_mbid: str, tracks: list[SourceTrack]) -> None:
        """Fetch `release/<mbid>?inc=recordings` and set `mb_recording_id` per track.

        Maps MB tracks to our `SourceTrack`s by `(disc_position, track_position)`.
        MB indexes both 1-based; our tracks may have `disc_no=None` for single-disc
        albums in which case we treat them as disc 1 for the lookup.
        """
        response = throttled_get(
            client,
            f"{MB_BASE}/release/{release_mbid}",
            host_key=MB_HOST_KEY,
            params={"inc": "recordings", "fmt": "json"},
        )
        response.raise_for_status()
        data = response.json()
        # Build an index: (disc_pos, track_pos) → recording MBID.
        recording_by_position: dict[tuple[int, int], str] = {}
        media = data.get("media") or []
        for medium in media:
            disc_pos = int(medium.get("position", 1) or 1)
            for mb_track in medium.get("tracks") or []:
                track_pos = mb_track.get("position")
                if track_pos is None:
                    continue
                recording = mb_track.get("recording") or {}
                rec_id = recording.get("id")
                if not rec_id:
                    continue
                recording_by_position[(disc_pos, int(track_pos))] = str(rec_id)
        if not recording_by_position:
            return
        # Mutate matching tracks in place. Albums with a single disc and no
        # explicit `disc_no` tag (the common case) are treated as disc 1.
        for track in tracks:
            if track.track_no is None:
                continue
            disc = track.disc_no if track.disc_no is not None else 1
            rec_id = recording_by_position.get((disc, track.track_no))
            if rec_id is not None:
                track.mb_recording_id = rec_id


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
