"""Online metadata + cover enrichment providers (off by default; `--enrich` opts in).

Today this runs MusicBrainz (release MBID lookup) → Cover Art Archive (front
cover fetch). The musichoarders.xyz site is intentionally **not** scraped — its
integration policy forbids automated use; instead we'll add a separate
`musickit cover-pick` command that opens it pre-filled for manual selection.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from musickit.cover import CoverCandidate
from musickit.metadata import AlbumSummary, MusicBrainzIds, SourceTrack


class EnrichmentResult(BaseModel):
    """What an enrichment pass returns for a single album."""

    musicbrainz: MusicBrainzIds | None = None
    extra_covers: list[CoverCandidate] = []
    notes: list[str] = []


class EnrichmentProvider(Protocol):
    """Pluggable provider interface — see musicbrainz.py / coverart.py."""

    name: str

    def enrich(self, album: AlbumSummary, tracks: list[SourceTrack]) -> EnrichmentResult:  # pragma: no cover
        """Look up additional metadata for `album`. Return an empty result on miss."""
        ...


def run_enrichment(album: AlbumSummary, tracks: list[SourceTrack]) -> EnrichmentResult:
    """Run MusicBrainz, then chain Cover Art Archive on the resolved MBID."""
    from musickit.enrich._http import get_client
    from musickit.enrich.coverart import CoverArtArchiveProvider
    from musickit.enrich.musicbrainz import MusicBrainzProvider

    merged = EnrichmentResult()
    client = get_client()
    try:
        mb_result = MusicBrainzProvider(client=client).enrich(album, tracks)
        merged.musicbrainz = mb_result.musicbrainz
        merged.notes.extend(mb_result.notes)

        if merged.musicbrainz and merged.musicbrainz.album_id:
            caa_result = CoverArtArchiveProvider(client=client).fetch(merged.musicbrainz.album_id)
            merged.extra_covers.extend(caa_result.extra_covers)
            merged.notes.extend(caa_result.notes)
    finally:
        client.close()

    return merged
