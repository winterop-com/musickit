"""Filesystem-safe name building for artist / album / track output paths."""

from __future__ import annotations

import re
import unicodedata

# Aliases that should all map to the canonical "Various Artists" folder name.
# Includes localised forms that show up on real rips (Swedish "Blandade
# Artister", German "Verschiedene Interpreten", Spanish "Varios Artistas",
# Italian "Vari Artisti", French "Artistes Divers"). Comparison is case-folded.
_VA_ALIASES: frozenset[str] = frozenset(
    {
        "va",
        "v.a.",
        "v/a",
        "various",
        "various artist",
        "various artists",
        # Swedish
        "blandade artister",
        "blandade artist",
        # German
        "verschiedene interpreten",
        "verschiedene",
        # Spanish
        "varios artistas",
        "varios",
        # Italian
        "vari artisti",
        # French
        "artistes divers",
        "divers",
    }
)

# Pattern matching scene-website "artists" like `LanzamientosMp3.es` /
# `boxset.me` / `mp3hosting.cc` / `www.0dayvinyls.org` — domain-shaped strings
# that some rip groups vandalise the artist (and sometimes album) tag with.
# Allows multi-label hosts (`www.foo.org`) but the final segment must be a
# 2–5 char TLD so single-letter acronyms (`R.E.M.`) and honorifics (`St. V`)
# don't accidentally match.
_SCENE_DOMAIN_RE = re.compile(r"^(?:[\w-]+\.)+[a-z]{2,5}\.?$", re.IGNORECASE)

# Patterns indicating a string still contains scene-rip residue that *should*
# have been cleaned by the convert pipeline. Used by `musickit library`
# auditing to flag rows the user might want to fix with `retag`.
_SCENE_RESIDUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Underscore between two ≥2-letter chunks (e.g. `Absolute_Music_45`).
    re.compile(r"(?<=[A-Za-z]{2})_(?=[A-Za-z]{2})"),
    # Dot between two ≥2-letter chunks (e.g. `Absolute.Music.60`); allows
    # `R.E.M.` and `St. Vincent` because of the 2-char minimum on each side.
    re.compile(r"(?<=[A-Za-z]{2})\.(?=[A-Za-z]{2})"),
    # Codec / quality / bitrate brackets that should have been stripped.
    re.compile(r"\[\s*(?:flac|mp3|aac|alac|wav|24\s*bit|16\s*bit|hi-?res|lossless|web)\s*\]", re.IGNORECASE),
    re.compile(r"\[\s*\d+\s*(?:k|kbps|kHz)\s*\]", re.IGNORECASE),
    # Leading `VA-` / `VA_` / `V.A.-` prefix.
    re.compile(r"^\s*(?:VA|V\.A\.)[\s_.\-]+", re.IGNORECASE),
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
    r"hi-?res|lossless|web|cd\s*rip|vinyl|"
    # Scene/release-site tags: bracketed `domain.tld` patterns like
    # `[nextorrent.com]`, `[example.org]`. Limited to known TLDs so we don't
    # strip catalog numbers or annotations like `[Live]` / `[Bonus]`.
    r"[a-z0-9-]+\.(?:com|org|net|to|is|cc|me|info|xyz|biz|uk|de|ru|tv|io)"
    r")\s*[\]\)]?",
    re.IGNORECASE,
)
_FOLDER_YEAR_RE = re.compile(r"[\(\[]?((?:19|20)\d{2})[\)\]]?")
# Year at the very start of the dir name (`1983. ` / `2012 - ` / `2007_`) —
# treated as a hand-curated canonical date that overrides reissue dates from
# track tags. Real example: `1983. Now That's What I Call Music! [2018 Reissue]`
# ships MP3s tagged `TDRC=2018`, but the user clearly intends 1983.
_LEADING_FOLDER_YEAR_RE = re.compile(r"^\s*((?:19|20)\d{2})[\s.\-_]")
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


def leading_year_from_folder(name: str | None) -> str | None:
    """Return the 4-digit year iff `name` starts with one followed by a separator.

    Used by the convert pipeline to override reissue years that survive in
    track tags when the input dir is hand-named with the original year (e.g.
    `1983. Album! [2018 Reissue]` should yield 1983, not 2018).
    """
    if not name:
        return None
    match = _LEADING_FOLDER_YEAR_RE.match(name)
    return match.group(1) if match else None


def is_various_artists(album_artist: str | None) -> bool:
    """Return True if `album_artist` indicates a Various-Artists compilation."""
    if not album_artist:
        return False
    return album_artist.strip().casefold() in _VA_ALIASES


_FOLDER_VA_PREFIX_RE = re.compile(
    # Matches `VA`, `V.A.`, `V_A`, `Various`, `Various Artists` at the start of a
    # folder name, followed by a separator (space/dash/dot/underscore).
    r"^\s*(?:V[\._]?A|various(?:[\s_]+artists?)?)[\s\.\-_]",
    re.IGNORECASE,
)


def folder_name_implies_va(name: str) -> bool:
    """True if a folder name like `VA-Absolute_Music_60` indicates a compilation.

    Used as a fallback signal when neither the album_artist tag nor a per-track
    artist majority can identify a compilation — common with scene-rip MP3
    directories whose tags were vandalised by domain-shaped junk.
    """
    if not name:
        return False
    return bool(_FOLDER_VA_PREFIX_RE.match(name))


def is_scene_residue(value: str | None) -> bool:
    """True if `value` still carries scene-rip residue that should have been cleaned.

    Used by audit tooling to flag album/artist names like `Absolute_Music_45`,
    `VA.-.Hits.2024`, or `Album [FLAC]` that the convert pipeline normally
    cleans up but might survive on already-converted libraries.
    """
    if not value:
        return False
    return any(pattern.search(value) for pattern in _SCENE_RESIDUE_PATTERNS)


def is_scene_domain_artist(value: str | None) -> bool:
    """True if the artist/album-artist tag is a scene-site domain (`xxx.es`).

    These are vandalism by rip groups, not real artist names. Callers should
    treat the value as missing so a downstream signal (per-track artist
    majority, compilation flag) wins instead.
    """
    if not value:
        return False
    return bool(_SCENE_DOMAIN_RE.match(value.strip()))


_TITLE_CASE_WORD_RE = re.compile(r"[A-Za-zÀ-ɏ]+(?:'[A-Za-zÀ-ɏ]+)?")


def smart_title_case(value: str | None) -> str | None:
    """Title-case `value` only when it appears to have been case-stripped.

    Heuristic: if the input contains any uppercase letter, return it unchanged
    (the source had real casing — `AC/DC`, `ABBA`, `iPhone`, `R.E.M.` should
    survive). Only when the source is entirely lowercase letters do we
    capitalize the first letter of each word, keeping apostrophe-suffix
    contractions intact (`don't` → `Don't`, not `Don'T`).
    """
    if value is None:
        return None
    if not value or any(c.isupper() for c in value):
        return value
    return _TITLE_CASE_WORD_RE.sub(lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), value)


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


def artist_folder(album_artist: str | None, fallback_artist: str | None, *, is_compilation: bool = False) -> str:
    """Folder name for the artist level. Maps VA / compilation albums to `Various Artists`.

    Three triggers route to the canonical `Various Artists` folder:
    - `album_artist` tag is a VA alias (`VA`, `V.A.`, `Various`, …)
    - `fallback_artist` (per-track majority) is itself a VA alias — some rips
      stamp `VA` as the per-track artist and leave `album_artist` empty
    - `is_compilation` is True (album-level signal: distinct per-track artists
      with no shared `album_artist` tag, e.g. an MP3 mix labelled only by
      filename)
    """
    if is_compilation or is_various_artists(album_artist) or is_various_artists(fallback_artist):
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
    track_total: int | None = None,
    extension: str = ".m4a",
) -> str:
    """Output filename for a single track.

    Default format: `01 - Title<ext>`. When the album spans multiple discs
    (`disc_total > 1`), the disc number is prefixed: `01-01 - Title<ext>`.
    When `artist` is provided (typically only for compilations / VA albums)
    it is inserted between track number and title: `01-05 - Artist - Title<ext>`.

    Track-number width grows with `track_total` so albums with ≥100 tracks
    sort alphabetically correctly: a 100-track album yields `001`, `002`,
    `010`, `099`, `100` instead of breaking at the 2/3-digit boundary.
    Disc-number width is fixed at 2 (no realistic disc count needs more).
    """
    width = 3 if (track_total or 0) >= 100 else 2
    track_str = f"{track_no:0{width}d}" if track_no else "0" * width
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
