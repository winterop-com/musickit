"""Read source audio tags (FLAC / MP3 / generic) and write MP4 ALAC tags.

Public API is split across submodules; this module re-exports the names
the rest of the project (and tests) import from `musickit.metadata`.
"""

from __future__ import annotations

from musickit.metadata.album import clean_album_title, summarize_album
from musickit.metadata.models import (
    SUPPORTED_AUDIO_EXTS,
    AlbumSummary,
    MusicBrainzIds,
    SourceTrack,
    TagOverrides,
)
from musickit.metadata.overrides import apply_tag_overrides
from musickit.metadata.read import read_source
from musickit.metadata.write import embed_cover_only, write_id3_tags, write_mp4_tags, write_tags

__all__ = [
    "SUPPORTED_AUDIO_EXTS",
    "AlbumSummary",
    "MusicBrainzIds",
    "SourceTrack",
    "TagOverrides",
    "apply_tag_overrides",
    "clean_album_title",
    "embed_cover_only",
    "read_source",
    "summarize_album",
    "write_id3_tags",
    "write_mp4_tags",
    "write_tags",
]
