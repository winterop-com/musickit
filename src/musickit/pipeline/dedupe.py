"""Source-side dedupe — drop duplicate tracks shipped under different filenames."""

from __future__ import annotations

from pathlib import Path

from musickit.metadata import SourceTrack


def _dedupe_duplicate_tracks(tracks: list[SourceTrack], warnings: list[str]) -> list[SourceTrack]:
    """Drop source-side duplicates that share `(disc, track, title, artist)` AND duration.

    Some rip groups ship every track twice under different filename
    conventions (`01. Artist - Title.flac` AND `01 Title.flac`) — same
    content, same tags. Without dedup the encoder produces a `(2)`-suffixed
    output for each. Audio-duration match (within 0.5s) discriminates these
    from genuinely-distinct tracks that happen to share a tag (e.g., a
    remix and its original at the same `track_no`); those keep both files
    via the downstream collision rename.
    """
    seen: dict[tuple[int, int, str, str], tuple[Path, float | None]] = {}
    deduped: list[SourceTrack] = []
    for track in tracks:
        if track.track_no is None and not track.title:
            deduped.append(track)
            continue
        key = (
            track.disc_no or 0,
            track.track_no or 0,
            (track.title or "").strip().casefold(),
            (track.artist or "").strip().casefold(),
        )
        if key in seen:
            kept_path, kept_duration = seen[key]
            same_duration = (
                kept_duration is not None
                and track.duration_s is not None
                and abs(track.duration_s - kept_duration) < 0.5
            )
            if same_duration:
                warnings.append(f"dropped duplicate of {kept_path.name}: {track.path.name}")
                continue
        deduped.append(track)
        if key not in seen:
            seen[key] = (track.path, track.duration_s)
    return deduped
