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
    duration_s: float = 0.0
    has_cover: bool = False
    cover_pixels: int = 0
    # When set, the TUI plays this URL instead of `path` — populated by the
    # Subsonic client mode so the same widgets/format helpers work for both
    # local files and remote streams.
    stream_url: str | None = None


class LibraryAlbum(BaseModel):
    """Album-level rollup with audit warnings populated by `audit()`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    artist_dir: str
    album_dir: str
    tag_album: str | None = None
    tag_year: str | None = None
    tag_album_artist: str | None = None
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
