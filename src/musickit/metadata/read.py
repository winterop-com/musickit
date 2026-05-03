"""Source-file tag readers — FLAC, MP3, MP4/M4A, and a generic mutagen fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import mutagen as _mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

from musickit.metadata.models import SourceTrack
from musickit.naming import smart_title_case


def read_source(
    path: Path,
    *,
    light: bool = False,
    measure_pictures: bool = False,
) -> SourceTrack:
    """Read tags + embedded cover from a single audio file.

    Source values that arrive entirely lowercase are smart-title-cased here
    so downstream filenames + tags display consistently. Anything with real
    casing (`AC/DC`, `ABBA`, `iPhone`, `R.E.M.`) is left alone.

    `light=True` skips the two expensive operations the convert pipeline
    needs but the library scanner / TUI doesn't:
      - Pillow decode of the embedded picture (for `cover_pixels`)
      - A second mutagen open to read `info.length` (for `duration_s`)
    `has_cover` still works in light mode (presence is checked without
    touching the bytes); only the pixel measurement is skipped.

    `measure_pictures=True` re-enables the Pillow decode even under
    `light=True`, so audit modes that need low-res-cover detection can
    pay just that cost without also paying the duration probe.
    """
    suffix = path.suffix.lower()
    if suffix == ".flac":
        track = _read_flac(path, light=light, measure_pictures=measure_pictures)
    elif suffix == ".mp3":
        track = _read_mp3(path, light=light, measure_pictures=measure_pictures)
    elif suffix in (".m4a", ".mp4", ".m4b"):
        track = _read_mp4(path, light=light, measure_pictures=measure_pictures)
    else:
        track = _read_generic(path, light=light, measure_pictures=measure_pictures)
    track.title = smart_title_case(track.title)
    track.artist = smart_title_case(track.artist)
    track.album = smart_title_case(track.album)
    track.album_artist = smart_title_case(track.album_artist)
    return track


def _read_flac(path: Path, *, light: bool = False, measure_pictures: bool = False) -> SourceTrack:
    flac = FLAC(path)
    tags: Any = flac.tags or {}
    track = SourceTrack(path=path)
    if flac.info is not None:
        track.duration_s = float(getattr(flac.info, "length", 0.0) or 0.0)
    track.title = _vorbis_first(tags, "title")
    track.artist = _vorbis_first(tags, "artist")
    track.album_artist = _vorbis_first(tags, "albumartist") or _vorbis_first(tags, "album artist")
    track.album = _vorbis_first(tags, "album")
    track.date = _vorbis_first(tags, "date") or _vorbis_first(tags, "year")
    track.genre = _vorbis_first(tags, "genre")

    track_no, track_total = _split_pos(_vorbis_first(tags, "tracknumber") or _vorbis_first(tags, "track"))
    if track_total is None:
        track_total = _to_int(_vorbis_first(tags, "tracktotal") or _vorbis_first(tags, "totaltracks"))
    track.track_no, track.track_total = track_no, track_total

    disc_no, disc_total = _split_pos(_vorbis_first(tags, "discnumber") or _vorbis_first(tags, "disc"))
    if disc_total is None:
        disc_total = _to_int(_vorbis_first(tags, "disctotal") or _vorbis_first(tags, "totaldiscs"))
    track.disc_no, track.disc_total = disc_no, disc_total

    track.bpm = _to_int(_vorbis_first(tags, "bpm"))
    track.label = _vorbis_first(tags, "label") or _vorbis_first(tags, "publisher")
    track.catalog = _vorbis_first(tags, "catalognumber") or _vorbis_first(tags, "labelno")
    track.lyrics = _vorbis_first(tags, "lyrics")

    for key in ("replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain", "replaygain_album_peak"):
        value = _vorbis_first(tags, key)
        if value:
            track.replaygain[key] = value

    pictures = list(flac.pictures or [])
    if pictures:
        # Prefer "front cover" (type 3); fall back to largest by pixel area.
        front = next((p for p in pictures if p.type == 3), None)
        chosen = front or max(pictures, key=lambda p: (p.width or 0) * (p.height or 0))
        if light and not measure_pictures:
            track.embedded_picture = b""  # presence-only sentinel
            track.embedded_picture_mime = chosen.mime or "image/jpeg"
            track.embedded_picture_pixels = 0
        elif light and measure_pictures:
            # FLAC pictures carry intrinsic dimensions in the tag block — no
            # Pillow decode needed even when we want pixel info.
            track.embedded_picture = b""
            track.embedded_picture_mime = chosen.mime or "image/jpeg"
            track.embedded_picture_pixels = (chosen.width or 0) * (chosen.height or 0)
        else:
            track.embedded_picture = bytes(chosen.data)
            track.embedded_picture_mime = chosen.mime or "image/jpeg"
            track.embedded_picture_pixels = (chosen.width or 0) * (chosen.height or 0)

    return track


def _read_mp3(path: Path, *, light: bool = False, measure_pictures: bool = False) -> SourceTrack:
    track = SourceTrack(path=path)
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        return track
    # Pull duration directly from the same MP3() open we'll use below for
    # validation — avoids the second mutagen open we used to do in
    # `read_source` for `info.length`.
    try:
        mp3 = MP3(path)
        track.duration_s = float(getattr(mp3.info, "length", 0.0) or 0.0)
    except Exception:  # pragma: no cover — duration is best-effort
        pass

    track.title = _id3_text(id3, "TIT2")
    track.artist = _id3_text(id3, "TPE1")
    track.album_artist = _id3_text(id3, "TPE2")
    track.album = _id3_text(id3, "TALB")
    track.date = _id3_text(id3, "TDRC") or _id3_text(id3, "TYER")
    track.genre = _id3_text(id3, "TCON")
    track.label = _id3_text(id3, "TPUB")
    track.lyrics = _id3_text(id3, "USLT::eng") or _id3_text(id3, "USLT")
    track.bpm = _to_int(_id3_text(id3, "TBPM"))

    track.track_no, track.track_total = _split_pos(_id3_text(id3, "TRCK"))
    track.disc_no, track.disc_total = _split_pos(_id3_text(id3, "TPOS"))

    apics = id3.getall("APIC")
    if apics:
        front = next((p for p in apics if getattr(p, "type", 0) == 3), None) or apics[0]
        track.embedded_picture_mime = front.mime or "image/jpeg"
        if light and not measure_pictures:
            track.embedded_picture = b""  # presence-only sentinel
            track.embedded_picture_pixels = 0
        elif light and measure_pictures:
            track.embedded_picture = b""
            track.embedded_picture_pixels = _measure_pixels(bytes(front.data))
        else:
            track.embedded_picture = bytes(front.data)
            # MP3 APICs don't carry intrinsic dims; either measure here or
            # leave 0 (the cover-pick code prefers a folder.jpg of known size).
            track.embedded_picture_pixels = _measure_pixels(track.embedded_picture)

    if not light:
        # Bit rate/duration aren't part of the tag bundle, but reading MP3() also validates the file.
        MP3(path)
    return track


def _read_mp4(path: Path, *, light: bool = False, measure_pictures: bool = False) -> SourceTrack:
    """Read tags + cover from an MP4/M4A (ALAC or AAC inside iTunes-style atoms)."""
    track = SourceTrack(path=path)
    mp4 = MP4(path)
    tags: Any = mp4.tags or {}
    track.duration_s = float(getattr(mp4.info, "length", 0.0) or 0.0)

    track.title = _mp4_first(tags, "\xa9nam")
    track.artist = _mp4_first(tags, "\xa9ART")
    track.album_artist = _mp4_first(tags, "aART")
    track.album = _mp4_first(tags, "\xa9alb")
    track.date = _mp4_first(tags, "\xa9day")
    track.genre = _mp4_first(tags, "\xa9gen")
    track.lyrics = _mp4_first(tags, "\xa9lyr")

    trkn = tags.get("trkn") if tags else None
    if trkn:
        first = trkn[0]
        if isinstance(first, tuple) and len(first) >= 2:
            track.track_no = first[0] or None
            track.track_total = first[1] or None

    disk = tags.get("disk") if tags else None
    if disk:
        first = disk[0]
        if isinstance(first, tuple) and len(first) >= 2:
            track.disc_no = first[0] or None
            track.disc_total = first[1] or None

    tmpo = tags.get("tmpo") if tags else None
    if tmpo:
        track.bpm = int(tmpo[0]) if tmpo else None

    label_ff = tags.get("----:com.apple.iTunes:LABEL") if tags else None
    if label_ff:
        track.label = bytes(label_ff[0]).decode("utf-8", errors="ignore")
    catalog_ff = tags.get("----:com.apple.iTunes:CATALOGNUMBER") if tags else None
    if catalog_ff:
        track.catalog = bytes(catalog_ff[0]).decode("utf-8", errors="ignore")
    if tags:
        for key, values in tags.items():
            if not isinstance(key, str) or not key.startswith("----:com.apple.iTunes:replaygain_"):
                continue
            name = key.rsplit(":", 1)[-1]
            if values:
                track.replaygain[name] = bytes(values[0]).decode("utf-8", errors="ignore")

    covers = tags.get("covr") if tags else None
    if covers:
        cover = covers[0]
        fmt = getattr(cover, "imageformat", None)
        track.embedded_picture_mime = "image/png" if fmt == MP4Cover.FORMAT_PNG else "image/jpeg"
        if light and not measure_pictures:
            track.embedded_picture = b""  # presence-only; skip Pillow + bytes copy
            track.embedded_picture_pixels = 0
        elif light and measure_pictures:
            # Audit's low-res-cover rule needs the dimension. Pay the Pillow
            # decode but skip the duration probe + bytes-copy retention.
            cover_bytes = bytes(cover)
            track.embedded_picture = b""  # don't retain the bytes
            track.embedded_picture_pixels = _measure_pixels(cover_bytes)
        else:
            track.embedded_picture = bytes(cover)
            track.embedded_picture_pixels = _measure_pixels(track.embedded_picture)

    return track


def _read_generic(path: Path, *, light: bool = False, measure_pictures: bool = False) -> SourceTrack:
    del light, measure_pictures  # generic reader pulls only basic tags, no pictures to skip
    track = SourceTrack(path=path)
    audio = _mutagen.File(path, easy=True)  # pyright: ignore[reportPrivateImportUsage]
    if audio is None:
        return track
    if audio.info is not None:
        track.duration_s = float(getattr(audio.info, "length", 0.0) or 0.0)
    if audio.tags is None:
        return track
    tags = audio.tags
    track.title = _easy_first(tags, "title")
    track.artist = _easy_first(tags, "artist")
    track.album_artist = _easy_first(tags, "albumartist")
    track.album = _easy_first(tags, "album")
    track.date = _easy_first(tags, "date")
    track.genre = _easy_first(tags, "genre")
    track.track_no, track.track_total = _split_pos(_easy_first(tags, "tracknumber"))
    track.disc_no, track.disc_total = _split_pos(_easy_first(tags, "discnumber"))
    track.bpm = _to_int(_easy_first(tags, "bpm"))
    return track


# ---------------------------------------------------------------------------
# Tag-format helpers
# ---------------------------------------------------------------------------


def _vorbis_first(tags: Any, key: str) -> str | None:
    if not tags:
        return None
    try:
        values = tags.get(key)
    except Exception:
        return None
    if not values:
        return None
    if isinstance(values, list):
        return str(values[0]) if values else None
    return str(values)


def _id3_text(id3: ID3, key: str) -> str | None:
    frame = id3.get(key)
    if frame is None:
        return None
    text = getattr(frame, "text", None)
    if text is None:
        return str(frame)
    if isinstance(text, list):
        return str(text[0]) if text else None
    return str(text)


def _mp4_first(tags: Any, key: str) -> str | None:
    if not tags:
        return None
    values = tags.get(key)
    if not values:
        return None
    return str(values[0])


def _easy_first(tags: Any, key: str) -> str | None:
    try:
        values = tags.get(key)
    except Exception:
        return None
    if not values:
        return None
    if isinstance(values, list):
        return str(values[0]) if values else None
    return str(values)


def _split_pos(value: str | None) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    if "/" in text:
        head, _, tail = text.partition("/")
        return _to_int(head), _to_int(tail)
    return _to_int(text), None


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"-?\d+", text)
    if not match:
        return None
    return int(match.group(0))


def _measure_pixels(data: bytes) -> int:
    try:
        import io as _io

        from PIL import Image  # local import to keep metadata module light

        with Image.open(_io.BytesIO(data)) as image:
            image.load()
            w, h = image.size
            return w * h
    except Exception:
        return 0
