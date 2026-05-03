"""Disc/track-number inference from filename shapes that scene rips love."""

from __future__ import annotations

import re

from musickit.discover import AlbumDir
from musickit.metadata import SourceTrack

_FILENAME_DISC_TRACK_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,3})\s*[.\-_]+\s*(.+)$")
_FILENAME_LEADING_3DIGIT_RE = re.compile(r"^\s*(\d{3})(?!\d)")


def _maybe_apply_filename_disc_track(album_dir: AlbumDir, tracks: list[SourceTrack]) -> None:
    """Apply `D-NN. Title.flac`-style disc/track encoding to all tracks.

    Triggers only when every track in the album matches the pattern AND at
    least two distinct disc numbers appear (so we don't accidentally treat
    `01-Title.flac` from a single-disc album as a multi-disc layout). Used
    by rips that put the disc + track in the filename rather than a CD
    subfolder (e.g. Zara Larsson 2-CD layout).
    """
    if album_dir.disc_total is not None:
        return  # discover already merged disc subfolders — trust that signal.
    parsed: list[tuple[int, int, str, SourceTrack]] = []
    for track in tracks:
        match = _FILENAME_DISC_TRACK_RE.match(track.path.stem)
        if not match:
            return
        parsed.append((int(match.group(1)), int(match.group(2)), match.group(3).strip(), track))
    discs = {disc_n for disc_n, _, _, _ in parsed}
    if len(discs) < 2:
        return
    disc_total = max(discs)
    for disc_n, track_n, title, track in parsed:
        track.disc_no = disc_n
        track.disc_total = disc_total
        if not track.track_no:
            track.track_no = track_n
        if not track.title:
            track.title = title


def _maybe_apply_scene_encoded_disc_track(album_dir: AlbumDir, tracks: list[SourceTrack]) -> None:
    """Decode scene-style `DTT` track numbers (`101 = disc 1 track 1`).

    Conventions like Now! / Absolute Music / Billboard compilations encode
    multi-disc structure into a 3-digit track number prefix on the FILENAME
    (`101_artist_-_title.mp3`). The MP3 `track` tag often carries the in-disc
    number (e.g. `1`) while the filename carries the encoded form (`101`).
    We read the FILENAME prefix because the tag is unreliable.

    Trigger conditions (all required, conservative):
    - `discover` did NOT already merge this album from disc subfolders.
    - Every track's filename starts with a 3-digit number (100-999).
    - The set of `tn // 100` has ≥2 unique values.
    - Each disc cluster has ≥3 tracks (a 100-track regular album with one
      track numbered 100 won't accidentally trigger this).

    On match, rewrite each track: `disc_no = filename_tn // 100`,
    `track_no = filename_tn % 100`, `disc_total = max(disc_no)`.
    """
    if album_dir.disc_total is not None:
        return
    filename_dtt: list[tuple[SourceTrack, int]] = []
    for track in tracks:
        match = _FILENAME_LEADING_3DIGIT_RE.match(track.path.stem)
        if not match:
            return  # at least one track lacks the prefix → bail
        tn = int(match.group(1))
        if not (100 <= tn < 1000):
            return
        filename_dtt.append((track, tn))
    discs: dict[int, int] = {}
    for _, tn in filename_dtt:
        discs[tn // 100] = discs.get(tn // 100, 0) + 1
    if len(discs) < 2 or any(count < 3 for count in discs.values()):
        return
    disc_total = max(discs)
    for track, tn in filename_dtt:
        track.disc_no = tn // 100
        track.track_no = tn % 100
        track.disc_total = disc_total
