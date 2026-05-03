"""Read source audio tags (FLAC / MP3 / generic) and write MP4 ALAC tags."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import mutagen as _mutagen
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
    TXXX,
    USLT,
)
from mutagen.id3._util import ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from pydantic import BaseModel, ConfigDict, Field

from musickit.naming import is_various_artists, smart_title_case

SUPPORTED_AUDIO_EXTS: frozenset[str] = frozenset(
    {".flac", ".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif"}
)

# Disc/CD markers we strip from album titles so multi-disc rips collapse to
# one clean name (`Album [CD1]` → `Album`). Three families:
# - explicit:    `[CD1]`, `(Disc 2)`, ` - CD 1`, `Disk2`
# - middle:      `Album (CD2) Live In Madrid` (Cranberries layout)
# - bare paren:  `Album (1)` where the number is the disc index, no keyword
_DISC_KEYWORD_RE = re.compile(
    # Period in the separator class so `Absolute Music 51 [CD.1]` strips cleanly.
    r"\s*[\[\(\-]?\s*(?:cd|disc|disk)[\s\-_.]*\d+\s*[\]\)]?",
    re.IGNORECASE,
)
_DISC_SUFFIX_RE = re.compile(
    r"\s*[\[\(\-]?\s*(?:cd|disc|disk)[\s\-_.]*\d+\s*[\]\)]?\s*$",
    re.IGNORECASE,
)
_BARE_DISC_PAREN_RE = re.compile(r"\s*\(\s*\d{1,2}\s*\)\s*$")


# Dot-as-word-separator scene-rip vandalism (`VA.-.Absolute.Music.60`).
# Replace dot only when surrounded by ≥2-char alphanumeric sequences on both
# sides — preserves `R.E.M.` (single-letter acronym), `St. Vincent` (space
# after dot), `Mr. Big` (same), and `vol.1`-style values are still 2/1-side
# so leave alone.
_SCENE_DOT_SEP_RE = re.compile(r"(?<=\w{2})\.(?=\w{2})")
# Underscore-as-word-separator: same shape, for `Absolute_Music_45`-style
# names. Bracketed by 2-letter word chunks on both sides so we don't touch
# legitimate identifiers (`__init__`, snake-case file fragments) — though
# album titles almost never carry those.
_SCENE_USCORE_SEP_RE = re.compile(r"(?<=\w{2})_(?=\w{2})")
_VA_PREFIX_IN_ALBUM_RE = re.compile(r"^\s*(?:VA|Various)[\s.\-]+", re.IGNORECASE)


def clean_album_title(album: str | None) -> str | None:
    """Clean disc markers, scene-rip dot-separators, and `VA -` prefixes from an album tag.

    Strips:
    - trailing `[CDx]` / `(Disc x)` / ` - CD 1` / `[CD.1]` markers
    - embedded `(CDx)` markers (Cranberries `Roses (CD2) Live In Madrid` shape)
    - trailing `(1)` / `(2)` (bare-paren disc index, no keyword)
    - dots / underscores used as word-separator instead of spaces
      (`Absolute.Music.60`, `Absolute_Music_45` → `Absolute Music 60/45`);
      preserves single-letter acronyms like `R.E.M.`
    - leading `VA - ` / `VA.-.` / `Various -` prefixes once the dots are space
    """
    if not album:
        return album
    cleaned = album
    # Repeatedly strip trailing disc markers (handles `Album [CD1] (Deluxe)`).
    while True:
        stripped = _DISC_SUFFIX_RE.sub("", cleaned).strip(" -")
        if stripped == cleaned:
            break
        cleaned = stripped
    # Strip embedded `(CDx)` markers (Cranberries Roses-style: `Roses (CD2) Live In Madrid`).
    cleaned_mid = _DISC_KEYWORD_RE.sub(" ", cleaned)
    cleaned_mid = re.sub(r"\s+", " ", cleaned_mid).strip(" -")
    if cleaned_mid:
        cleaned = cleaned_mid
    # Last pass: strip trailing `(1)` / `(2)` etc. (disc number without a keyword).
    bare = _BARE_DISC_PAREN_RE.sub("", cleaned).strip(" -")
    if bare:
        cleaned = bare
    # Dots/underscores as separator: replace between multi-letter chunks with space.
    cleaned = _SCENE_DOT_SEP_RE.sub(" ", cleaned)
    cleaned = _SCENE_USCORE_SEP_RE.sub(" ", cleaned)
    # Strip leading VA prefix (now that dots are spaces, `VA.-.Foo` reads
    # as `VA - Foo` / `VA.-.Foo`; either way the prefix should go).
    cleaned = _VA_PREFIX_IN_ALBUM_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned or album


class SourceTrack(BaseModel):
    """Tag bundle read from a single source audio file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    date: str | None = None
    genre: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    disc_total: int | None = None
    bpm: int | None = None
    label: str | None = None
    catalog: str | None = None
    lyrics: str | None = None
    replaygain: dict[str, str] = Field(default_factory=dict)
    embedded_picture: bytes | None = None
    embedded_picture_mime: str | None = None
    embedded_picture_pixels: int = 0
    duration_s: float | None = None  # audio duration; used by dedup to discriminate same-tag distinct content


class AlbumSummary(BaseModel):
    """Album-level rollup derived by majority-vote across the album's tracks."""

    album: str | None = None
    album_artist: str | None = None
    artist_fallback: str | None = None
    year: str | None = None
    genre: str | None = None
    track_total: int | None = None
    disc_total: int | None = None
    is_compilation: bool = False
    label: str | None = None
    catalog: str | None = None


class MusicBrainzIds(BaseModel):
    """MusicBrainz IDs supplied by an --enrich provider."""

    album_id: str | None = None
    artist_id: str | None = None
    release_group_id: str | None = None
    track_id: str | None = None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


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


def summarize_album(tracks: list[SourceTrack]) -> AlbumSummary:
    """Build an album-level summary by majority-vote across `tracks`.

    For multi-disc albums the album-name vote is biased toward disc 1 — bonus
    discs often carry tags like `Album (CD2) Live In ...` that would otherwise
    win on count and produce a misleading combined name.
    """
    disc_one_tracks = [t for t in tracks if t.disc_no == 1]
    album_source = disc_one_tracks if disc_one_tracks else tracks
    # Album name should be unanimous within a real album. Require quorum so a
    # single stray tagged track (foreign album mixed into the rip) can't
    # impersonate the whole-album value when most tracks have no album tag.
    album = clean_album_title(_majority((t.album for t in album_source), quorum=True))
    album_artist = _majority(t.album_artist for t in tracks)
    year = _majority(t.date for t in tracks)
    genre = _majority(t.genre for t in tracks)
    label = _majority(t.label for t in tracks)
    catalog = _majority(t.catalog for t in tracks)

    artist_counts = Counter(t.artist for t in tracks if t.artist)
    distinct_artists = len(artist_counts)
    artist_fallback = artist_counts.most_common(1)[0][0] if artist_counts else None

    track_total = max((t.track_total or 0 for t in tracks), default=0) or len(tracks) or None
    disc_total = max((t.disc_total or 0 for t in tracks), default=0) or None

    # Compilation if: album_artist is a VA alias, the per-track artist majority
    # is itself a VA alias (rips that leave album_artist empty but stamp every
    # track artist as `VA`), or there's no album_artist + tracks span multiple
    # different artists.
    is_compilation = (
        is_various_artists(album_artist)
        or is_various_artists(artist_fallback)
        or (album_artist is None and distinct_artists > 1)
    )

    return AlbumSummary(
        album=album,
        album_artist=album_artist,
        artist_fallback=artist_fallback,
        year=year,
        genre=genre,
        track_total=track_total,
        disc_total=disc_total,
        is_compilation=is_compilation,
        label=label,
        catalog=catalog,
    )


# ---------------------------------------------------------------------------
# Writing — dispatches by output extension
# ---------------------------------------------------------------------------


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
        _set_freeform(tags, "MusicBrainz Track Id", musicbrainz.track_id)

    if cover_bytes:
        cover_format = MP4Cover.FORMAT_PNG if (cover_mime or "").lower().endswith("png") else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=cover_format)]

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
            ("MusicBrainz Track Id", musicbrainz.track_id),
        ]
        for desc, mb_value in mb_pairs:
            if mb_value:
                id3.add(TXXX(encoding=3, desc=desc, text=mb_value))

    if cover_bytes:
        mime = "image/png" if (cover_mime or "").lower().endswith("png") else "image/jpeg"
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="Front cover", data=cover_bytes))

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


class TagOverrides(BaseModel):
    """Optional tag overrides applied in-place by `apply_tag_overrides`.

    Each field is `None` to mean "leave the existing tag alone". Pass an empty
    string to *clear* a tag explicitly (rare; typically you just leave it).
    """

    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    year: str | None = None
    genre: str | None = None
    track_total: int | None = None
    disc_total: int | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in self.model_dump().values())


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
    if ov.track_total is not None:
        existing_trkn = tags.get("trkn") or [(0, 0)]
        first_trkn = existing_trkn[0]
        current_no = first_trkn[0] if isinstance(first_trkn, tuple) else 0
        tags["trkn"] = [(current_no, ov.track_total)]
    if ov.disc_total is not None:
        existing_disk = tags.get("disk") or [(0, 0)]
        first_disk = existing_disk[0]
        current_disc = first_disk[0] if isinstance(first_disk, tuple) else 0
        tags["disk"] = [(current_disc, ov.disc_total)]
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
    if ov.track_total is not None:
        existing = id3.get("TRCK")
        current_no = ""
        if existing and existing.text:
            current_no = str(existing.text[0]).split("/", 1)[0]
        id3.delall("TRCK")
        id3.add(TRCK(encoding=3, text=f"{current_no or '0'}/{ov.track_total}"))
    if ov.disc_total is not None:
        existing_disc = id3.get("TPOS")
        current_disc = ""
        if existing_disc and existing_disc.text:
            current_disc = str(existing_disc.text[0]).split("/", 1)[0]
        id3.delall("TPOS")
        id3.add(TPOS(encoding=3, text=f"{current_disc or '0'}/{ov.disc_total}"))
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
    if ov.track_total is not None:
        flac["TRACKTOTAL"] = [str(ov.track_total)]
    if ov.disc_total is not None:
        flac["DISCTOTAL"] = [str(ov.disc_total)]
    flac.save()


# ---------------------------------------------------------------------------
# FLAC / MP3 / generic readers
# ---------------------------------------------------------------------------


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


def _mp4_first(tags: Any, key: str) -> str | None:
    if not tags:
        return None
    values = tags.get(key)
    if not values:
        return None
    return str(values[0])


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
# Helpers
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


def _majority(values: Any, *, quorum: bool = False) -> str | None:
    """Most common non-empty value across `values`.

    With `quorum=True`, the winner must occur on at least half of *all* values
    (including empties). This guards against a stray tag from a misfiled
    track impersonating the album-wide value when most tracks are blank — a
    real album is unanimous on its own album name.
    """
    materialized = list(values)
    counts: Counter[str] = Counter(v for v in materialized if v)
    if not counts:
        return None
    winner, count = counts.most_common(1)[0]
    if quorum:
        threshold = max(1, (len(materialized) + 1) // 2)
        if count < threshold:
            return None
    return winner


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
