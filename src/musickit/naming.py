"""Filesystem-safe name building for artist / album / track output paths."""

from __future__ import annotations

import re
import unicodedata

# Aliases that should all map to the canonical "Various Artists" folder name.
_VA_ALIASES: frozenset[str] = frozenset(
    {
        "va",
        "v.a.",
        "v/a",
        "various",
        "various artist",
        "various artists",
    }
)

VARIOUS_ARTISTS = "Various Artists"

# Characters that aren't legal across macOS / Windows / Linux filesystems.
_BAD_CHARS = str.maketrans(
    {
        "/": "-",
        "\\": "-",
        ":": " -",
        "*": "",
        "?": "",
        '"': "'",
        "<": "",
        ">": "",
        "|": "-",
        "\x00": "",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")
# Trim trailing whitespace; preserve trailing dots (e.g. `R.E.M.`).
_TRAILING_BAD_RE = re.compile(r" +$")
_MAX_COMPONENT_BYTES = 180


_FOLDER_TAG_RE = re.compile(
    r"\s*[\[\(]\s*(?:"
    r"flac|mp3|aac|alac|wav|ape|ogg|opus|"
    r"\d+\s*bit(?:[-\s]\d+(?:\.\d+)?\s*kHz)?|"  # 24bit, 16Bit-44.1kHz
    r"\d+(?:\.\d+)?\s*kHz|"
    r"hi-?res|lossless|web|cd\s*rip|vinyl"
    r")\s*[\]\)]?",
    re.IGNORECASE,
)
_FOLDER_YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?")
_VA_PREFIX_RE = re.compile(r"^\s*(?:VA|Various)\s*-\s*", re.IGNORECASE)


def clean_folder_album_name(name: str) -> tuple[str, str | None]:
    """Strip codec/quality tags + extract year from a folder name.

    Returns `(cleaned_album_name, year_or_None)`. Used as a fallback when an
    album has no `ALBUM` tag and we have to lean on the folder name. Also
    drops a leading `VA -` / `Various -` prefix since that's compilation
    noise rather than part of the album title.
    """
    year_match = _FOLDER_YEAR_RE.search(name)
    year = year_match.group(1) if year_match else None
    cleaned = name
    if year_match:
        cleaned = cleaned.replace(year_match.group(0), " ")
    cleaned = _FOLDER_TAG_RE.sub(" ", cleaned)
    cleaned = _VA_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return cleaned or name, year


def is_various_artists(album_artist: str | None) -> bool:
    """Return True if `album_artist` indicates a Various-Artists compilation."""
    if not album_artist:
        return False
    return album_artist.strip().casefold() in _VA_ALIASES


def sanitize_component(value: str) -> str:
    """Make `value` safe to use as a single path component on any OS.

    Replaces forbidden characters, collapses whitespace, NFC-normalizes unicode,
    strips trailing dots/spaces, and caps the encoded length at 180 bytes.
    """
    cleaned = unicodedata.normalize("NFC", value).translate(_BAD_CHARS)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = _TRAILING_BAD_RE.sub("", cleaned)
    if not cleaned:
        cleaned = "Unknown"
    encoded = cleaned.encode("utf-8")
    if len(encoded) > _MAX_COMPONENT_BYTES:
        # Truncate on a unicode-safe boundary by progressively decoding.
        cleaned = encoded[:_MAX_COMPONENT_BYTES].decode("utf-8", errors="ignore").rstrip()
    return cleaned


def artist_folder(album_artist: str | None, fallback_artist: str | None) -> str:
    """Folder name for the artist level. Maps VA aliases to `Various Artists`.

    Checks both the album-artist tag and the fallback (per-track artist majority)
    against VA aliases — some compilation rips put `VA` as the per-track artist
    while leaving `album_artist` empty.
    """
    if is_various_artists(album_artist) or is_various_artists(fallback_artist):
        return VARIOUS_ARTISTS
    name = (album_artist or "").strip() or (fallback_artist or "").strip() or "Unknown Artist"
    return sanitize_component(name)


def album_folder(album: str | None, year: str | int | None) -> str:
    """Folder name for the album level.

    Format: `YYYY - Album` so directory listings inside an artist folder sort
    chronologically. Year is omitted if unknown, falling back to just `Album`.
    A year that's part of the album title (e.g. `Vocal Trance Hits 2024`,
    Taylor Swift's `1989`) is intentionally left in place — it's the actual
    title.
    """
    base = (album or "").strip() or "Unknown Album"
    year_str = _coerce_year(year)
    full = f"{year_str} - {base}" if year_str else base
    return sanitize_component(full)


def track_filename(
    track_no: int | None,
    title: str | None,
    *,
    artist: str | None = None,
    disc_no: int | None = None,
    disc_total: int | None = None,
    extension: str = ".m4a",
) -> str:
    """Output filename for a single track.

    Default format: `01 - Title<ext>`. When the album spans multiple discs
    (`disc_total > 1`), the disc number is prefixed: `01-01 - Title<ext>`.
    When `artist` is provided (typically only for compilations / VA albums)
    it is inserted between track number and title: `01-05 - Artist - Title<ext>`.
    """
    track_str = f"{track_no:02d}" if track_no else "00"
    title_str = (title or "").strip() or "Untitled"
    title_str = sanitize_component(title_str)
    if disc_total and disc_total > 1 and disc_no:
        prefix = f"{disc_no:02d}-{track_str}"
    else:
        prefix = track_str
    ext = extension if extension.startswith(".") else f".{extension}"
    if artist:
        artist_str = sanitize_component(artist.strip())
        return f"{prefix} - {artist_str} - {title_str}{ext.lower()}"
    return f"{prefix} - {title_str}{ext.lower()}"


def _coerce_year(year: str | int | None) -> str | None:
    if year is None:
        return None
    if isinstance(year, int):
        return str(year) if year > 0 else None
    text = year.strip()
    if not text:
        return None
    # Tags often look like "2012-09-04" — keep only the leading 4-digit year.
    match = re.match(r"(\d{4})", text)
    return match.group(1) if match else None
