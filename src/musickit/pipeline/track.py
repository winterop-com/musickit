"""Per-track planning + encode/tag work."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from musickit import convert, naming
from musickit.convert import OutputFormat
from musickit.cover import Cover
from musickit.metadata import AlbumSummary, MusicBrainzIds, SourceTrack, write_tags
from musickit.pipeline.filenames import _parse_filename_for_va, _title_from_filename, _track_no_from_filename


class _ResolvedTrack(BaseModel):
    """The (title, track_no, artist) triplet derived from tags + filename fallbacks.

    Computed once per track in the planning phase. The resolver never mutates
    the input `SourceTrack`; the worker thread reads from this resolved view
    when it needs the output filename or rewriting tags. Keeps the planning
    loop and the encode loop in lockstep on the same parsing rules.
    """

    title: str
    track_no: int | None
    artist: str | None


def _resolve_track_metadata(track: SourceTrack, summary: AlbumSummary) -> _ResolvedTrack:
    """Compute the user-visible title/track_no/artist for `track`.

    Read-only on the input. The rules:
    - Use tag values when present.
    - When the title is missing or the track's "artist" is a VA placeholder
      (`VA`, `Various`, …), parse the filename for `NN - [VA - ]Artist - Title`.
    - On a compilation where the title still contains ` - ` after a VA
      placeholder artist, split title into per-track artist + title.
    - Fall back to plain `_title_from_filename` / `_track_no_from_filename`
      when nothing else applies.
    """
    title = track.title
    artist = track.artist
    track_no = track.track_no
    artist_is_va_marker = naming.is_various_artists(artist)

    parsed_artist: str | None = None
    parsed_title: str | None = None
    if not title or (summary.is_compilation and (artist_is_va_marker or not artist)):
        parsed_artist, parsed_title = _parse_filename_for_va(track.path)
        if parsed_title and (not title or artist_is_va_marker):
            title = parsed_title
        if parsed_artist and (artist_is_va_marker or not artist):
            artist = parsed_artist

    # Title still has `Artist - Title` shape on a comp track? Split it.
    if summary.is_compilation and naming.is_various_artists(artist) and title and " - " in title:
        head, _, tail = title.partition(" - ")
        if head.strip() and tail.strip():
            artist = head.strip()
            title = tail.strip()

    if not title:
        title = _title_from_filename(track.path)
    if not track_no:
        track_no = _track_no_from_filename(track.path)

    return _ResolvedTrack(title=title, track_no=track_no, artist=artist)


def _planned_filename(
    track: SourceTrack,
    summary: AlbumSummary,
    fmt: OutputFormat,
    copy_only: bool,
    *,
    resolved: _ResolvedTrack | None = None,
) -> str:
    """Compute the destination filename for `track` without touching the disk."""
    res = resolved or _resolve_track_metadata(track, summary)
    output_ext = ".m4a" if (copy_only and fmt is not OutputFormat.MP3) else fmt.extension
    filename_artist = res.artist if summary.is_compilation else None
    return naming.track_filename(
        res.track_no,
        res.title,
        artist=filename_artist,
        disc_no=track.disc_no,
        disc_total=track.disc_total or summary.disc_total,
        track_total=track.track_total or summary.track_total,
        extension=output_ext,
    )


def _process_track(
    track: SourceTrack,
    summary: AlbumSummary,
    out_dir: Path,
    cover: Cover | None,
    musicbrainz: MusicBrainzIds | None,
    *,
    fmt: OutputFormat,
    bitrate: str,
    copy_only: bool = False,
    forced_filename: str,
    resolved: _ResolvedTrack,
) -> str:
    """Encode + tag one track. Returns the output filename for verbose logging."""
    # Apply resolver output to the track so write_tags emits the cleaned values
    # — this is the only place we mutate, and it's a worker-local copy in the
    # threadpool by virtue of `track` being the unique per-track object.
    track.title = resolved.title
    track.artist = resolved.artist
    track.track_no = resolved.track_no
    out_path = out_dir / forced_filename

    if copy_only and fmt is OutputFormat.MP3:
        # MP3 → MP3: byte-for-byte copy. Keeps the file as a plain `.mp3` so
        # Finder, Music.app and every player can read its ID3 tags.
        convert.copy_passthrough(track.path, out_path)
    elif copy_only:
        # AAC m4a → fresh m4a remux (audio bytes preserved).
        convert.remux_to_m4a(track.path, out_path)
    else:
        convert.encode(track.path, out_path, fmt, bitrate=bitrate)
    write_tags(
        out_path,
        track,
        summary,
        cover_bytes=cover.data if cover else None,
        cover_mime=cover.mime if cover else None,
        musicbrainz=musicbrainz,
    )
    return forced_filename
