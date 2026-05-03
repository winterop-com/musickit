"""Walk a converted-output directory and build a `LibraryIndex`."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from musickit import naming
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.metadata import SUPPORTED_AUDIO_EXTS, read_source

_ALBUM_DIR_YEAR_RE = re.compile(r"^(\d{4})\s*-\s*(.+)$")


def scan(
    root: Path,
    *,
    on_album: Callable[[Path, int, int], None] | None = None,
    measure_pictures: bool = False,
) -> LibraryIndex:
    """Walk `root` and build a `LibraryIndex` from every album dir found.

    An album dir = any directory directly containing ≥1 supported audio file.
    Multi-disc albums in this layout are flat (single dir with `01-NN`/`02-NN`
    filenames), so no merge logic is needed here — `convert` already produced
    them that way.

    `on_album(album_dir, idx, total)` is called once per album right before its
    tracks are read, where `idx` is 1-indexed and `total` is the album count
    (known from the cheap pre-walk in `_iter_album_dirs`). The CLI uses this
    to drive a progress bar over slow filesystems / network drives.

    `measure_pictures=True` enables embedded-cover dimension measurement
    (Pillow decode) so the audit's low-res-cover rule has data to work with.
    Adds noticeable scan time per cover, so it's opt-in — used by the CLI's
    `--audit` and `--issues-only` modes.
    """
    album_dirs = _iter_album_dirs(root)
    total = len(album_dirs)
    albums: list[LibraryAlbum] = []
    for idx, album_dir in enumerate(album_dirs, start=1):
        if on_album is not None:
            on_album(album_dir, idx, total)
        album = _scan_album(album_dir, measure_pictures=measure_pictures)
        albums.append(album)
    albums.sort(key=lambda a: (a.artist_dir.lower(), a.album_dir.lower()))
    return LibraryIndex(root=root, albums=albums)


def _iter_album_dirs(root: Path) -> list[Path]:
    """Return every directory under `root` containing ≥1 supported audio file."""
    if not root.is_dir():
        return []
    seen: set[Path] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            continue
        seen.add(path.parent)
    return sorted(seen)


def _scan_album(album_dir: Path, *, measure_pictures: bool = False) -> LibraryAlbum:
    audio_files = sorted(p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS)
    tracks: list[LibraryTrack] = []
    for audio_path in audio_files:
        try:
            source = read_source(audio_path, light=True, measure_pictures=measure_pictures)
        except Exception:
            continue
        track = LibraryTrack(
            path=audio_path,
            title=source.title,
            artist=source.artist,
            album_artist=source.album_artist,
            album=source.album,
            year=_year_only(source.date),
            genre=source.genre,
            track_no=source.track_no,
            disc_no=source.disc_no,
            duration_s=source.duration_s or 0.0,
            # `light` mode uses a presence-only sentinel (b"") for embedded
            # pictures — skipping the byte copy and Pillow decode but still
            # reporting cover presence accurately.
            has_cover=source.embedded_picture is not None,
            cover_pixels=source.embedded_picture_pixels,
        )
        source.embedded_picture = None
        tracks.append(track)

    artist_dir = album_dir.parent.name
    return LibraryAlbum(
        path=album_dir,
        artist_dir=artist_dir,
        album_dir=album_dir.name,
        tag_album=_majority(t.album for t in tracks),
        tag_year=_majority(t.year for t in tracks),
        tag_album_artist=_majority(t.album_artist for t in tracks),
        tag_genre=_majority(t.genre for t in tracks),
        track_count=len(tracks),
        disc_count=max((t.disc_no or 1 for t in tracks), default=1),
        is_compilation=naming.is_various_artists(artist_dir),
        has_cover=any(t.has_cover for t in tracks),
        cover_pixels=max((t.cover_pixels for t in tracks), default=0),
        tracks=tracks,
        warnings=[],
    )


def _majority(values: Iterable[str | None]) -> str | None:
    counts: Counter[str] = Counter(v for v in values if v)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _year_only(date: str | None) -> str | None:
    if not date:
        return None
    match = re.match(r"(\d{4})", date.strip())
    return match.group(1) if match else None


def _split_dir_year(album_dir: str) -> tuple[str | None, str]:
    """Parse `2012 - Night Visions` into `("2012", "Night Visions")`."""
    match = _ALBUM_DIR_YEAR_RE.match(album_dir)
    if match:
        return match.group(1), match.group(2)
    return None, album_dir
