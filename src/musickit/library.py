"""Walk a converted-output directory, build an Artist→Album→Track index, audit it.

Reads tags via `metadata.read_source` (cover bytes are dropped immediately
after reading; we only keep `cover_pixels` and `has_cover`). Audit rules
flag albums the user might want to fix with `retag` / `cover` / re-convert.

`fix_*` helpers close the loop: for warnings that have a deterministic
action (MB-derivable year, dir/tag rename), they call `apply_tag_overrides`
and rename the album dir directly.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from musickit import naming
from musickit.metadata import SUPPORTED_AUDIO_EXTS, TagOverrides, apply_tag_overrides, read_source

if TYPE_CHECKING:
    from rich.console import Console

_ALBUM_DIR_YEAR_RE = re.compile(r"^(\d{4})\s*-\s*(.+)$")
_LOW_RES_THRESHOLD_PIXELS = 500 * 500


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
    has_cover: bool = False
    cover_pixels: int = 0


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


class LibraryIndex(BaseModel):
    """Full library index, sorted by `(artist_dir, album_dir)`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    root: Path
    albums: list[LibraryAlbum] = []


def scan(
    root: Path,
    *,
    on_album: Callable[[Path, int, int], None] | None = None,
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
    """
    album_dirs = _iter_album_dirs(root)
    total = len(album_dirs)
    albums: list[LibraryAlbum] = []
    for idx, album_dir in enumerate(album_dirs, start=1):
        if on_album is not None:
            on_album(album_dir, idx, total)
        album = _scan_album(album_dir)
        albums.append(album)
    albums.sort(key=lambda a: (a.artist_dir.lower(), a.album_dir.lower()))
    return LibraryIndex(root=root, albums=albums)


def audit(index: LibraryIndex) -> None:
    """Append audit findings to each `album.warnings` in-place."""
    for album in index.albums:
        _audit_album(album)


# ---------------------------------------------------------------------------
# Scanner internals
# ---------------------------------------------------------------------------


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


def _scan_album(album_dir: Path) -> LibraryAlbum:
    audio_files = sorted(p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS)
    tracks: list[LibraryTrack] = []
    for audio_path in audio_files:
        try:
            source = read_source(audio_path, light=True)
        except Exception:
            continue
        track = LibraryTrack(
            path=audio_path,
            title=source.title,
            artist=source.artist,
            album_artist=source.album_artist,
            album=source.album,
            year=_year_only(source.date),
            track_no=source.track_no,
            disc_no=source.disc_no,
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


# ---------------------------------------------------------------------------
# Audit rules
# ---------------------------------------------------------------------------


def _audit_album(album: LibraryAlbum) -> None:
    _audit_cover(album)
    _audit_year(album)
    _audit_album_artist(album)
    _audit_album_name(album)
    _audit_artist_name(album)
    _audit_tag_path_mismatch(album)
    _audit_track_gaps(album)
    _audit_track_count(album)


def _audit_cover(album: LibraryAlbum) -> None:
    if not album.has_cover:
        album.warnings.append("no cover")
        return
    if album.cover_pixels and album.cover_pixels < _LOW_RES_THRESHOLD_PIXELS:
        album.warnings.append(f"low-res cover ({album.cover_pixels} px)")


def _audit_year(album: LibraryAlbum) -> None:
    years = {t.year for t in album.tracks if t.year}
    if not years:
        album.warnings.append("missing year")
    elif len(years) > 1:
        album.warnings.append(f"mixed years: {sorted(years)}")


def _audit_album_artist(album: LibraryAlbum) -> None:
    if album.is_compilation:
        return
    distinct = {t.album_artist for t in album.tracks if t.album_artist}
    if len(distinct) > 1:
        album.warnings.append(f"mixed album_artist: {sorted(distinct)}")


def _audit_album_name(album: LibraryAlbum) -> None:
    _, dir_album = _split_dir_year(album.album_dir)
    if naming.is_scene_residue(dir_album):
        album.warnings.append(f"scene residue in album dir: {dir_album!r}")
    if album.tag_album and naming.is_scene_residue(album.tag_album):
        album.warnings.append(f"scene residue in album tag: {album.tag_album!r}")
    if album.album_dir.lower().startswith("unknown"):
        album.warnings.append("album dir is 'Unknown'")


def _audit_artist_name(album: LibraryAlbum) -> None:
    if naming.is_scene_residue(album.artist_dir):
        album.warnings.append(f"scene residue in artist dir: {album.artist_dir!r}")
    if naming.is_scene_domain_artist(album.artist_dir):
        album.warnings.append(f"scene-domain artist dir: {album.artist_dir!r}")
    if album.artist_dir.lower() == "unknown artist":
        album.warnings.append("artist is 'Unknown Artist'")


def _audit_tag_path_mismatch(album: LibraryAlbum) -> None:
    if not album.tag_album:
        return
    _, dir_album = _split_dir_year(album.album_dir)
    if _normalise_for_compare(album.tag_album) != _normalise_for_compare(dir_album):
        album.warnings.append(f"tag/path mismatch: tag={album.tag_album!r} dir={dir_album!r}")


def _audit_track_gaps(album: LibraryAlbum) -> None:
    by_disc: dict[int, list[int]] = {}
    for track in album.tracks:
        if track.track_no is None:
            continue
        disc = track.disc_no or 1
        by_disc.setdefault(disc, []).append(track.track_no)
    for disc, numbers in by_disc.items():
        numbers.sort()
        if not numbers:
            continue
        expected = set(range(1, max(numbers) + 1))
        missing = sorted(expected - set(numbers))
        if missing:
            disc_label = f"disc {disc} " if len(by_disc) > 1 else ""
            album.warnings.append(f"{disc_label}track gaps: missing {missing}")


def _audit_track_count(album: LibraryAlbum) -> None:
    if album.track_count == 0:
        album.warnings.append("no tracks read")


def _normalise_for_compare(value: str) -> str:
    """Lowercase + NFC + strip whitespace so dir/tag album comparisons aren't case-/accent-sensitive."""
    return unicodedata.normalize("NFC", value).strip().casefold()


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------


def fix_index(
    index: LibraryIndex,
    *,
    dry_run: bool = False,
    console: Console | None = None,
    year_lookup: object | None = None,
) -> list[str]:
    """Apply deterministic fixes to every flagged album in `index`.

    Returns a list of human-readable action lines. `year_lookup` is the
    MusicBrainz year-lookup callable (defaults to
    `enrich.musicbrainz.lookup_release_year` — injectable for tests).
    """
    if year_lookup is None:
        from musickit.enrich.musicbrainz import lookup_release_year

        year_lookup = lookup_release_year

    actions: list[str] = []
    for album in index.albums:
        if not album.warnings:
            continue
        actions.extend(fix_album(album, dry_run=dry_run, console=console, year_lookup=year_lookup))
    return actions


def fix_album(
    album: LibraryAlbum,
    *,
    dry_run: bool = False,
    console: Console | None = None,
    year_lookup: object,
) -> list[str]:
    """Apply fixes to one album. Returns the action lines performed (or planned)."""
    actions: list[str] = []
    label = f"{album.artist_dir} / {album.album_dir}"

    # Missing-year fixes go first so the rename below sees the new year.
    if any("missing year" in w for w in album.warnings):
        new_year = _fix_missing_year(album, dry_run=dry_run, year_lookup=year_lookup)
        if new_year:
            actions.append(f"{label}: year ← {new_year} (musicbrainz)")
            if console is not None:
                console.print(f"[green]✓[/green] {label}: year ← {new_year} (musicbrainz)")

    # Tag/path mismatch — rename the directory to match the (now-current) tag.
    needs_rename = any(w.startswith("tag/path mismatch") for w in album.warnings) or bool(actions)
    if needs_rename:
        renamed = _fix_rename_to_match_tag(album, dry_run=dry_run)
        if renamed:
            actions.append(f"{label}: renamed dir → {renamed}")
            if console is not None:
                console.print(f"[green]✓[/green] {label}: renamed dir → {renamed}")

    return actions


def _fix_missing_year(
    album: LibraryAlbum,
    *,
    dry_run: bool,
    year_lookup: object,
) -> str | None:
    if not album.tag_album:
        return None
    artist_query = album.tag_album_artist or album.artist_dir
    if naming.is_various_artists(artist_query):
        artist_query = "Various Artists"
    year = year_lookup(album.tag_album, artist_query)  # type: ignore[operator]
    if not isinstance(year, str) or not year:
        return None
    if dry_run:
        return year
    overrides = TagOverrides(year=year)
    for track in album.tracks:
        try:
            apply_tag_overrides(track.path, overrides)
        except Exception:  # pragma: no cover — surface elsewhere
            return None
    # Reflect the change in the in-memory model so downstream rename uses it.
    for track in album.tracks:
        track.year = year
    album.tag_year = year
    return year


def _fix_rename_to_match_tag(album: LibraryAlbum, *, dry_run: bool) -> str | None:
    """Rename `album.path` to `YYYY - Album` based on current tag values."""
    if not album.tag_album:
        return None
    new_name = naming.album_folder(album.tag_album, album.tag_year)
    if new_name == album.album_dir:
        return None
    new_path = album.path.parent / new_name
    if new_path.exists() and new_path.resolve() != album.path.resolve():
        return None  # don't clobber an existing dir
    if dry_run:
        return new_name
    album.path.rename(new_path)
    album.path = new_path
    album.album_dir = new_name
    return new_name
