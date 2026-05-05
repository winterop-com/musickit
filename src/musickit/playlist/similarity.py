"""Tag-based similarity score for `LibraryTrack` pairs.

The score is the sum of weighted signals already present in the index —
no audio analysis, no remote lookups. Higher = more similar. A negative
score is possible (e.g. compilation/non-compilation mismatch).

The weights were tuned by hand on a real 1200-album library. Two design
calls worth noting:

  - `album_artist` is preferred over `artist` for the artist-match
    bonus, because compilations and feature credits otherwise dominate
    the pool with the wrong primary artist.
  - Genre matches are token-set based: a track tagged "Indie Rock /
    Alternative" matches another tagged "Alternative Rock" through the
    shared "Alternative" token.
"""

from __future__ import annotations

from musickit.library.models import LibraryTrack


def _primary_artist(track: LibraryTrack) -> str | None:
    """`album_artist` first, fall back to `artist`."""
    return track.album_artist or track.artist


def _genre_tokens(track: LibraryTrack) -> set[str]:
    """Lowercased single-word genre tokens.

    Splits on `/ , ; & -` AND whitespace so genre-tag wording
    differences match through their shared words. Examples:

      "Indie Rock / Alternative"  ->  {"indie", "rock", "alternative"}
      "Alternative Rock"          ->  {"alternative", "rock"}
      "Alt-Rock"                  ->  {"alt", "rock"}

    All three overlap on `rock`, which is the behaviour the scorer
    wants. Without the whitespace split, `"indie rock"` and
    `"alternative rock"` would be different tokens and the genre
    signal would rarely fire across tag-style differences.
    """
    raw: list[str] = []
    if track.genre:
        raw.append(track.genre)
    raw.extend(track.genres)
    tokens: set[str] = set()
    for entry in raw:
        for sep in ("/", ",", ";", "&", "-"):
            entry = entry.replace(sep, " ")
        for tok in entry.split():
            tok = tok.strip().lower()
            if tok:
                tokens.add(tok)
    return tokens


def _year_int(track: LibraryTrack) -> int | None:
    """First 4-digit year embedded in `track.year`, or None."""
    if not track.year:
        return None
    digits = "".join(ch for ch in track.year if ch.isdigit())
    if len(digits) < 4:
        return None
    try:
        return int(digits[:4])
    except ValueError:
        return None


def score(seed: LibraryTrack, candidate: LibraryTrack) -> float:
    """Higher = more similar to `seed`. Range roughly [-3.0, +10.0]."""
    if seed.path == candidate.path:
        return float("-inf")

    s = 0.0

    # Artist signal — strongest. `album_artist` first to avoid feature
    # credits / "Various Artists" pulling unrelated tracks.
    seed_artist = _primary_artist(seed)
    cand_artist = _primary_artist(candidate)
    if seed_artist and cand_artist and seed_artist.lower() == cand_artist.lower():
        s += 5.0

    # Genre signal — token-set overlap.
    seed_tokens = _genre_tokens(seed)
    cand_tokens = _genre_tokens(candidate)
    if seed_tokens and cand_tokens and seed_tokens & cand_tokens:
        s += 3.0

    # Year proximity.
    seed_year = _year_int(seed)
    cand_year = _year_int(candidate)
    if seed_year is not None and cand_year is not None:
        delta = abs(seed_year - cand_year)
        if delta == 0:
            s += 2.0
        elif delta <= 5:
            s += 1.0
        elif delta <= 15:
            s += 0.5

    # Penalise mixing compilations with non-compilations. The data we
    # have is per-album (`is_compilation`); we surface it via track tags
    # only when both sides actually have an album_artist, since "Various
    # Artists" is the convention.
    seed_va = (seed.album_artist or "").lower() == "various artists"
    cand_va = (cand_artist or "").lower() == "various artists"
    if seed_va != cand_va:
        s -= 3.0

    return s
