"""Album-title cleanup and album-level majority-vote summary."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from musickit.metadata.models import AlbumSummary, SourceTrack
from musickit.naming import is_various_artists

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
# Word-form disc suffixes that some box sets ship: `Greatest Hits I / Disc One`,
# `Album - Disc Three`. Covers one through twelve (most box sets stop well short).
_DISC_WORD_SUFFIX_RE = re.compile(
    r"\s*[\[\(\-/]?\s*(?:cd|disc|disk)\s+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s*[\]\)]?\s*$",
    re.IGNORECASE,
)
_BARE_DISC_PAREN_RE = re.compile(r"\s*\(\s*\d{1,2}\s*\)\s*$")
# Trailing separators left behind after disc-suffix stripping (` /`, ` -`, etc).
_TRAILING_SEPARATOR_RE = re.compile(r"\s*[/\-]\s*$")


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

# Heuristics for "this album_artist value is folder-name noise, not an
# actual artist name." Conservative: fires only when the value contains
# bracket markup or an explicit box-set keyword AND the per-track
# artist appears as a substring (so we have a clean fallback).
_NOISY_ALBUM_ARTIST_HINTS_RE = re.compile(r"[\[\(]|\bbox\s*set\b|\bcd\d|\b\d+cd\b", re.IGNORECASE)


def clean_album_artist(album_artist: str | None, artist_fallback: str | None) -> str | None:
    """Reject obvious folder-name noise as an album_artist value.

    Some rips put the box-set folder name into TPE2 (e.g. ``"Greatest
    Hits I, II & III (The Platinum Collection) [3CD Box Set] - Queen"``)
    instead of the actual artist (``"Queen"``). When that happens AND
    we have a clean per-track artist that appears as a substring of
    the noisy value, prefer the per-track artist.

    Returns ``album_artist`` unchanged when the value looks plausibly
    legitimate (no bracket / box-set markers) — Various Artists box
    sets like ``"Various Artists"`` or short clean values pass through.
    """
    if not album_artist or not artist_fallback:
        return album_artist
    if not _NOISY_ALBUM_ARTIST_HINTS_RE.search(album_artist):
        return album_artist
    if artist_fallback.lower() in album_artist.lower():
        return artist_fallback
    return album_artist


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
        # Both numeric (`Disc 2`) and word-form (`Disc Two`) suffixes — some
        # box sets ship the latter, e.g. Queen `Greatest Hits I / Disc One`.
        stripped = _DISC_SUFFIX_RE.sub("", cleaned).strip(" -")
        stripped = _DISC_WORD_SUFFIX_RE.sub("", stripped).strip(" -")
        # Any leftover trailing separator (slash / dash / etc.) — common
        # after stripping `Album / Disc One` → `Album /`.
        stripped = _TRAILING_SEPARATOR_RE.sub("", stripped).strip()
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
    raw_album_artist = _majority(t.album_artist for t in tracks)
    year = _majority(t.date for t in tracks)
    genre = _majority(t.genre for t in tracks)
    label = _majority(t.label for t in tracks)
    catalog = _majority(t.catalog for t in tracks)

    artist_counts = Counter(t.artist for t in tracks if t.artist)
    distinct_artists = len(artist_counts)
    artist_fallback = artist_counts.most_common(1)[0][0] if artist_counts else None

    # Now apply the album_artist cleanup against the resolved per-track
    # artist majority. Done after artist_fallback so we have something
    # to fall back to.
    album_artist = clean_album_artist(raw_album_artist, artist_fallback)

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
