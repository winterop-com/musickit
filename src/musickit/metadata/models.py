"""Tag-bundle data classes shared across the metadata package."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

SUPPORTED_AUDIO_EXTS: frozenset[str] = frozenset(
    {".flac", ".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif"}
)


class SourceTrack(BaseModel):
    """Tag bundle read from a single source audio file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    date: str | None = None
    genre: str | None = None
    # Multi-genre support — FLAC repeats GENRE, ID3 repeats TCON, MP4 atoms
    # only carry one. `genre` stays as the primary (backwards-compat) and
    # equals `genres[0]` when both are populated.
    genres: list[str] = Field(default_factory=list)
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    disc_total: int | None = None
    bpm: int | None = None
    label: str | None = None
    catalog: str | None = None
    lyrics: str | None = None
    replaygain: dict[str, str] = Field(default_factory=dict)
    embedded_picture: bytes | None = None
    embedded_picture_mime: str | None = None
    embedded_picture_pixels: int = 0
    duration_s: float | None = None  # audio duration; used by dedup to discriminate same-tag distinct content
    # MusicBrainz recording MBID (per-track, distinct from the album-level
    # release MBID). Populated by the MB enrichment follow-up call when
    # `--enrich` is on. Picard convention: stored as `MusicBrainz Track Id`
    # on MP4 freeform / `MusicBrainz Recording Id` on ID3 TXXX.
    mb_recording_id: str | None = None


class AlbumSummary(BaseModel):
    """Album-level rollup derived by majority-vote across the album's tracks."""

    album: str | None = None
    album_artist: str | None = None
    artist_fallback: str | None = None
    year: str | None = None
    genre: str | None = None
    track_total: int | None = None
    disc_total: int | None = None
    is_compilation: bool = False
    label: str | None = None
    catalog: str | None = None


class MusicBrainzIds(BaseModel):
    """Album-level MusicBrainz IDs supplied by an --enrich provider.

    Per-track recording MBIDs live on `SourceTrack.mb_recording_id` —
    they vary per track and don't belong on an album-scope object.
    """

    album_id: str | None = None
    artist_id: str | None = None
    release_group_id: str | None = None


class TagOverrides(BaseModel):
    """Optional tag overrides applied in-place by `apply_tag_overrides`.

    Each field is `None` to mean "leave the existing tag alone". Pass an empty
    string to *clear* a tag explicitly (rare; typically you just leave it).
    """

    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    year: str | None = None
    genre: str | None = None
    track_total: int | None = None
    disc_total: int | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in self.model_dump().values())
