"""Tag writers — MP3 (ID3v2.4) + MP4/M4A + cover-only embed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.id3._frames import (  # pyright: ignore[reportPrivateImportUsage]
    APIC,
    TALB,
    TBPM,
    TCMP,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TPUB,
    TRCK,
    TSSE,
    TXXX,
    USLT,
)
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

from musickit import __version__ as MUSICKIT_VERSION
from musickit.metadata.models import AlbumSummary, MusicBrainzIds, SourceTrack


def _encoder_tag() -> str:
    r"""Encoder string written into every output file (MP4 `\xa9too` / ID3 `TSSE`).

    `musickit inspect` and any tag editor surfaces this, so a stray
    file weeks later still tells you which release produced it —
    useful when chasing regressions across versions.
    """
    return f"musickit {MUSICKIT_VERSION}"


def write_tags(
    path: Path,
    track: SourceTrack,
    album: AlbumSummary,
    *,
    cover_bytes: bytes | None,
    cover_mime: str | None,
    musicbrainz: MusicBrainzIds | None = None,
) -> None:
    """Write the target tag set to `path`, dispatching by file extension."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        write_id3_tags(path, track, album, cover_bytes=cover_bytes, cover_mime=cover_mime, musicbrainz=musicbrainz)
    elif suffix in (".m4a", ".mp4", ".m4b"):
        write_mp4_tags(path, track, album, cover_bytes=cover_bytes, cover_mime=cover_mime, musicbrainz=musicbrainz)
    else:
        raise ValueError(f"unsupported output extension for tag writing: {suffix}")


def write_mp4_tags(
    path: Path,
    track: SourceTrack,
    album: AlbumSummary,
    *,
    cover_bytes: bytes | None,
    cover_mime: str | None,
    musicbrainz: MusicBrainzIds | None = None,
) -> None:
    """Write the full target tag set to an existing ALAC/AAC `.m4a` file."""
    mp4 = MP4(path)
    tags = mp4.tags
    if tags is None:
        mp4.add_tags()
        tags = mp4.tags
    assert tags is not None  # appease type-checker; add_tags always populates

    tags.clear()
    _set(tags, "\xa9nam", track.title)
    _set(tags, "\xa9ART", track.artist or album.artist_fallback)
    _set(tags, "\xa9alb", album.album)
    _set(tags, "aART", "Various Artists" if album.is_compilation else (album.album_artist or album.artist_fallback))
    _set(tags, "\xa9day", _year_only(album.year))
    _set(tags, "\xa9gen", track.genre or album.genre)
    _set(tags, "\xa9lyr", track.lyrics)

    track_no = track.track_no or 0
    track_total = track.track_total or album.track_total or 0
    if track_no or track_total:
        tags["trkn"] = [(track_no, track_total)]

    disc_no = track.disc_no or 0
    disc_total = track.disc_total or album.disc_total or 0
    if disc_no or disc_total:
        tags["disk"] = [(disc_no, disc_total)]

    if track.bpm is not None and track.bpm > 0:
        tags["tmpo"] = [int(track.bpm)]

    if album.is_compilation:
        tags["cpil"] = True

    label = track.label or album.label
    catalog = track.catalog or album.catalog
    _set_freeform(tags, "LABEL", label)
    _set_freeform(tags, "CATALOGNUMBER", catalog)
    for key, value in track.replaygain.items():
        _set_freeform(tags, key, value)

    if musicbrainz:
        _set_freeform(tags, "MusicBrainz Album Id", musicbrainz.album_id)
        _set_freeform(tags, "MusicBrainz Artist Id", musicbrainz.artist_id)
        _set_freeform(tags, "MusicBrainz Release Group Id", musicbrainz.release_group_id)
    # Per-track recording MBID — Picard convention is to store this as
    # the iTunes "MusicBrainz Track Id" freeform (despite the name, it's
    # the recording MBID, not the release-track MBID). Written even when
    # no album-level musicbrainz block is present, since per-track lookups
    # via AcoustID can produce recording IDs without an album hit.
    if track.mb_recording_id:
        _set_freeform(tags, "MusicBrainz Track Id", track.mb_recording_id)

    if cover_bytes:
        cover_format = MP4Cover.FORMAT_PNG if (cover_mime or "").lower().endswith("png") else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=cover_format)]

    # iTunes-style "encoder" atom — read by `musickit inspect`, ffprobe,
    # and most tag editors as the encoder/encoding-tool field. Always
    # last so it overwrites any inherited value.
    _set(tags, "\xa9too", _encoder_tag())

    mp4.save()


def write_id3_tags(
    path: Path,
    track: SourceTrack,
    album: AlbumSummary,
    *,
    cover_bytes: bytes | None,
    cover_mime: str | None,
    musicbrainz: MusicBrainzIds | None = None,
) -> None:
    """Write the full target tag set to an MP3 file as ID3v2.4."""
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()

    id3.delete()

    title = track.title
    artist = track.artist or album.artist_fallback
    album_artist = "Various Artists" if album.is_compilation else (album.album_artist or album.artist_fallback)
    year = _year_only(album.year)
    genre = track.genre or album.genre

    if title:
        id3.add(TIT2(encoding=3, text=title))
    if artist:
        id3.add(TPE1(encoding=3, text=artist))
    if album.album:
        id3.add(TALB(encoding=3, text=album.album))
    if album_artist:
        id3.add(TPE2(encoding=3, text=album_artist))
    if year:
        id3.add(TDRC(encoding=3, text=year))
    if genre:
        id3.add(TCON(encoding=3, text=genre))

    track_no = track.track_no or 0
    track_total = track.track_total or album.track_total or 0
    if track_no or track_total:
        id3.add(TRCK(encoding=3, text=f"{track_no}/{track_total}" if track_total else str(track_no)))

    disc_no = track.disc_no or 0
    disc_total = track.disc_total or album.disc_total or 0
    if disc_no or disc_total:
        id3.add(TPOS(encoding=3, text=f"{disc_no}/{disc_total}" if disc_total else str(disc_no)))

    if track.bpm is not None and track.bpm > 0:
        id3.add(TBPM(encoding=3, text=str(int(track.bpm))))

    if album.is_compilation:
        id3.add(TCMP(encoding=3, text="1"))

    label = track.label or album.label
    if label:
        id3.add(TPUB(encoding=3, text=label))

    if track.lyrics:
        id3.add(USLT(encoding=3, lang="eng", desc="", text=track.lyrics))

    catalog = track.catalog or album.catalog
    if catalog:
        id3.add(TXXX(encoding=3, desc="CATALOGNUMBER", text=catalog))

    for key, value in track.replaygain.items():
        id3.add(TXXX(encoding=3, desc=key, text=value))

    if musicbrainz:
        mb_pairs: list[tuple[str, str | None]] = [
            ("MusicBrainz Album Id", musicbrainz.album_id),
            ("MusicBrainz Artist Id", musicbrainz.artist_id),
            ("MusicBrainz Release Group Id", musicbrainz.release_group_id),
        ]
        for desc, mb_value in mb_pairs:
            if mb_value:
                id3.add(TXXX(encoding=3, desc=desc, text=mb_value))
    # Per-track recording MBID — Picard's `MusicBrainz Recording Id` TXXX
    # frame. Independent of the album-level block above so that AcoustID-
    # only lookups can still emit a recording MBID.
    if track.mb_recording_id:
        id3.add(TXXX(encoding=3, desc="MusicBrainz Recording Id", text=track.mb_recording_id))

    if cover_bytes:
        mime = "image/png" if (cover_mime or "").lower().endswith("png") else "image/jpeg"
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Front cover", data=cover_bytes))

    # ID3v2.4 "Software/hardware and settings used for encoding" — the
    # ID3 equivalent of MP4's `\xa9too`. Mirrors what we write in
    # `write_mp4_tags`; same release-traceability use case.
    id3.add(TSSE(encoding=3, text=_encoder_tag()))

    id3.save(path, v2_version=4)


def embed_cover_only(path: Path, *, cover_bytes: bytes, cover_mime: str) -> None:
    """Replace the cover of an existing audio file without touching other tags.

    Supports `.m4a/.mp4/.m4b`, `.mp3`, and `.flac`. Used by `musickit cover`
    to retrofit album art onto already-converted files. All previous pictures
    are dropped first so we don't end up with multiple covers.
    """
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".mp4", ".m4b"):
        mp4 = MP4(path)
        if mp4.tags is None:
            mp4.add_tags()
        tags = mp4.tags
        assert tags is not None
        cover_format = MP4Cover.FORMAT_PNG if cover_mime.lower().endswith("png") else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=cover_format)]
        mp4.save()
        return
    if suffix == ".mp3":
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        for apic_key in list(id3.keys()):
            if apic_key.startswith("APIC"):
                del id3[apic_key]
        mime = "image/png" if cover_mime.lower().endswith("png") else "image/jpeg"
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Front cover", data=cover_bytes))
        id3.save(path, v2_version=4)
        return
    if suffix == ".flac":
        from mutagen.flac import Picture  # local import — only needed for FLAC

        flac = FLAC(path)
        flac.clear_pictures()
        picture = Picture()
        picture.type = 3  # front cover
        picture.mime = "image/png" if cover_mime.lower().endswith("png") else "image/jpeg"
        picture.data = cover_bytes
        flac.add_picture(picture)
        flac.save()
        return
    raise ValueError(f"unsupported audio file for cover injection: {path}")


# ---------------------------------------------------------------------------
# Helpers (also used by `overrides.py`)
# ---------------------------------------------------------------------------


def _year_only(date: str | None) -> str | None:
    if not date:
        return None
    match = re.match(r"(\d{4})", date.strip())
    return match.group(1) if match else None


def _set(tags: Any, key: str, value: str | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    tags[key] = [text]


def _set_or_clear(tags: Any, key: str, value: str) -> None:
    """Like `_set` but treats an empty string as "delete this tag".

    Used by `apply_tag_overrides` for MP4, where the contract is that
    empty string clears the tag (matching ID3's and FLAC's behaviour).
    """
    text = value.strip()
    if not text:
        if key in tags:
            del tags[key]
        return
    tags[key] = [text]


def _set_freeform(tags: Any, name: str, value: str | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    full_key = f"----:com.apple.iTunes:{name}"
    from mutagen.mp4 import MP4FreeForm  # local import — only needed for MP4 writing

    tags[full_key] = [MP4FreeForm(text.encode("utf-8"), dataformat=1)]
