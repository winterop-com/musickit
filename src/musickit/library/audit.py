"""Audit rules — appended to `album.warnings` by `audit()`."""

from __future__ import annotations

import unicodedata

from musickit import naming
from musickit.library.models import LibraryAlbum, LibraryIndex
from musickit.library.scan import _split_dir_year

_LOW_RES_THRESHOLD_PIXELS = 500 * 500


def audit(index: LibraryIndex) -> None:
    """Replace each `album.warnings` with a fresh set of audit findings.

    Idempotent: running twice on the same index gives the same warnings,
    not duplicates. The per-album rescan path relies on this so it can
    re-audit a single album after a tag fix.
    """
    for album in index.albums:
        audit_album(album)


def audit_album(album: LibraryAlbum) -> None:
    """Replace `album.warnings` with a fresh audit pass for one album.

    Warnings are sorted alphabetically at the end so the in-memory
    `LibraryIndex` produced by `scan_full` matches the one produced by
    `load` (SQLite returns `album_warnings` rows ORDER BY warning).
    """
    album.warnings = []
    _audit_cover(album)
    _audit_year(album)
    _audit_album_artist(album)
    _audit_album_name(album)
    _audit_artist_name(album)
    _audit_tag_path_mismatch(album)
    _audit_track_gaps(album)
    _audit_track_count(album)
    album.warnings.sort()


# Back-compat alias — older callers/tests may still import the underscored name.
_audit_album = audit_album


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

    # Some VA / mega-comp rips number tracks continuously across discs (disc
    # 2's track 1 is "track 10" because disc 1 had 9 tracks). Per-disc audit
    # starting at 1 would falsely flag the missing 1-9 on disc 2. Detect the
    # pattern: every disc D > min starts at the previous disc's max + 1.
    sorted_discs = sorted(by_disc)
    is_continuous = len(sorted_discs) > 1 and all(
        d - 1 in by_disc and min(by_disc[d]) == max(by_disc[d - 1]) + 1 for d in sorted_discs[1:]
    )

    for disc, numbers in by_disc.items():
        numbers.sort()
        if not numbers:
            continue
        # Continuous numbering → gaps within (min..max) on this disc.
        # Per-disc-restart numbering → gaps from 1..max as before.
        start = min(numbers) if is_continuous else 1
        expected = set(range(start, max(numbers) + 1))
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
