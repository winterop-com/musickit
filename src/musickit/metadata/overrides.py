"""In-place tag overrides for MP3 / MP4 / FLAC — `musickit retag`'s back-end."""

from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.id3._frames import (  # pyright: ignore[reportPrivateImportUsage]
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
)
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp4 import MP4

from musickit.metadata.models import TagOverrides
from musickit.metadata.write import _set_or_clear, _year_only


def apply_tag_overrides(path: Path, overrides: TagOverrides) -> None:
    """Apply `overrides` to `path` in-place; leave unspecified tags untouched.

    Supports `.m4a/.mp4/.m4b`, `.mp3`, `.flac`. Track totals get merged into
    the existing `(track, total)` tuple so we don't lose the per-track number.
    """
    if overrides.is_empty():
        return
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".mp4", ".m4b"):
        _apply_overrides_mp4(path, overrides)
        return
    if suffix == ".mp3":
        _apply_overrides_id3(path, overrides)
        return
    if suffix == ".flac":
        _apply_overrides_flac(path, overrides)
        return
    raise ValueError(f"unsupported audio file for tag override: {path}")


def _apply_overrides_mp4(path: Path, ov: TagOverrides) -> None:
    mp4 = MP4(path)
    if mp4.tags is None:
        mp4.add_tags()
    tags = mp4.tags
    assert tags is not None
    # `_set_or_clear` honours the TagOverrides empty-string-means-clear
    # contract for MP4 atoms. Plain `_set` silently no-ops on empty
    # strings, which would leave the old value in place.
    if ov.title is not None:
        _set_or_clear(tags, "\xa9nam", ov.title)
    if ov.artist is not None:
        _set_or_clear(tags, "\xa9ART", ov.artist)
    if ov.album is not None:
        _set_or_clear(tags, "\xa9alb", ov.album)
    if ov.album_artist is not None:
        _set_or_clear(tags, "aART", ov.album_artist)
    if ov.year is not None:
        _set_or_clear(tags, "\xa9day", _year_only(ov.year) or ov.year if ov.year else "")
    if ov.genre is not None:
        _set_or_clear(tags, "\xa9gen", ov.genre)
    if ov.track_no is not None or ov.track_total is not None:
        existing_trkn = tags.get("trkn") or [(0, 0)]
        first_trkn = existing_trkn[0] if existing_trkn else (0, 0)
        cur_no, cur_total = first_trkn if isinstance(first_trkn, tuple) else (0, 0)
        new_no = ov.track_no if ov.track_no is not None else cur_no
        new_total = ov.track_total if ov.track_total is not None else cur_total
        tags["trkn"] = [(new_no, new_total)]
    if ov.disc_no is not None or ov.disc_total is not None:
        existing_disk = tags.get("disk") or [(0, 0)]
        first_disk = existing_disk[0] if existing_disk else (0, 0)
        cur_no, cur_total = first_disk if isinstance(first_disk, tuple) else (0, 0)
        new_no = ov.disc_no if ov.disc_no is not None else cur_no
        new_total = ov.disc_total if ov.disc_total is not None else cur_total
        tags["disk"] = [(new_no, new_total)]
    mp4.save()


def _apply_overrides_id3(path: Path, ov: TagOverrides) -> None:
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    if ov.title is not None:
        id3.delall("TIT2")
        if ov.title:
            id3.add(TIT2(encoding=3, text=ov.title))
    if ov.artist is not None:
        id3.delall("TPE1")
        if ov.artist:
            id3.add(TPE1(encoding=3, text=ov.artist))
    if ov.album is not None:
        id3.delall("TALB")
        if ov.album:
            id3.add(TALB(encoding=3, text=ov.album))
    if ov.album_artist is not None:
        id3.delall("TPE2")
        if ov.album_artist:
            id3.add(TPE2(encoding=3, text=ov.album_artist))
    if ov.year is not None:
        id3.delall("TDRC")
        year_value = _year_only(ov.year) or ov.year
        if year_value:
            id3.add(TDRC(encoding=3, text=year_value))
    if ov.genre is not None:
        id3.delall("TCON")
        if ov.genre:
            id3.add(TCON(encoding=3, text=ov.genre))
    if ov.track_no is not None or ov.track_total is not None:
        existing = id3.get("TRCK")
        cur_no, cur_total = "", ""
        if existing and existing.text:
            parts = str(existing.text[0]).split("/", 1)
            cur_no = parts[0]
            cur_total = parts[1] if len(parts) > 1 else ""
        new_no = str(ov.track_no) if ov.track_no is not None else (cur_no or "0")
        new_total = str(ov.track_total) if ov.track_total is not None else cur_total
        text = f"{new_no}/{new_total}" if new_total else new_no
        id3.delall("TRCK")
        id3.add(TRCK(encoding=3, text=text))
    if ov.disc_no is not None or ov.disc_total is not None:
        existing_disc = id3.get("TPOS")
        cur_no, cur_total = "", ""
        if existing_disc and existing_disc.text:
            parts = str(existing_disc.text[0]).split("/", 1)
            cur_no = parts[0]
            cur_total = parts[1] if len(parts) > 1 else ""
        new_no = str(ov.disc_no) if ov.disc_no is not None else (cur_no or "0")
        new_total = str(ov.disc_total) if ov.disc_total is not None else cur_total
        text = f"{new_no}/{new_total}" if new_total else new_no
        id3.delall("TPOS")
        id3.add(TPOS(encoding=3, text=text))
    id3.save(path, v2_version=4)


def _apply_overrides_flac(path: Path, ov: TagOverrides) -> None:
    flac = FLAC(path)
    if flac.tags is None:
        flac.add_tags()
    year_value = (_year_only(ov.year) or ov.year) if ov.year is not None else None
    pairs: list[tuple[str, str | None]] = [
        ("TITLE", ov.title),
        ("ARTIST", ov.artist),
        ("ALBUM", ov.album),
        ("ALBUMARTIST", ov.album_artist),
        ("DATE", year_value),
        ("GENRE", ov.genre),
    ]
    for key, value in pairs:
        if value is None:
            continue
        if value == "":
            if key in flac:
                del flac[key]
        else:
            flac[key] = [value]
    if ov.track_no is not None:
        flac["TRACKNUMBER"] = [str(ov.track_no)]
    if ov.track_total is not None:
        flac["TRACKTOTAL"] = [str(ov.track_total)]
    if ov.disc_no is not None:
        flac["DISCNUMBER"] = [str(ov.disc_no)]
    if ov.disc_total is not None:
        flac["DISCTOTAL"] = [str(ov.disc_total)]
    flac.save()
