"""Data models for the library index — track / album / index BaseModels."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class LibraryTrack(BaseModel):
    """Track-level summary used by `LibraryIndex`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    year: str | None = None
    track_no: int | None = None
    disc_no: int | None = None
    genre: str | None = None
    genres: list[str] = []
    lyrics: str | None = None
    # ReplayGain values from source tags (`replaygain_track_gain`,
    # `replaygain_album_gain`, `..._peak`). Empty dict when the source had
    # no RG tags. AudioPlayer uses these to normalise level differences
    # between tracks during local playback.
    replaygain: dict[str, str] = {}
    duration_s: float = 0.0
    has_cover: bool = False
    cover_pixels: int = 0
    # When set, the TUI plays this URL instead of `path` — populated by the
    # Subsonic client mode so the same widgets/format helpers work for both
    # local files and remote streams.
    stream_url: str | None = None
    # ISO 8601 timestamp the track was starred at, or None when not starred.
    # Populated by Subsonic-client mode from `getAlbum` / `getStarred2`
    # responses (the server enriches every payload via `StarStore.enrich`).
    # The TUI surfaces it as a ♥ glyph in the track row; toggling fires
    # `/rest/star` / `/rest/unstar` and rewrites this field optimistically.
    starred: str | None = None


class LibraryAlbum(BaseModel):
    """Album-level rollup with audit warnings populated by `audit()`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    artist_dir: str
    album_dir: str
    tag_album: str | None = None
    tag_year: str | None = None
    tag_album_artist: str | None = None
    tag_genre: str | None = None
    track_count: int = 0
    disc_count: int = 1
    is_compilation: bool = False
    has_cover: bool = False
    cover_pixels: int = 0
    tracks: list[LibraryTrack] = []
    warnings: list[str] = []
    # When set, this album was sourced from a Subsonic server with this ID.
    # The TUI uses it to lazy-load tracks via `getAlbum?id=...` when the
    # user opens the album, instead of pre-fetching every track at launch.
    subsonic_id: str | None = None


class LibraryIndex(BaseModel):
    """Full library index, sorted by `(artist_dir, album_dir)`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    albums: list[LibraryAlbum] = []
