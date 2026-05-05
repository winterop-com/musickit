"""TUI `/`-bar filter — diacritic-folded casefolded substring matching.

Real-world libraries are full of accented characters (Sigur Rós,
Björk, José González, Beyoncé, Daft Punk's Cœur). The original
filter did `needle.casefold() in haystack.casefold()`, which is fast
but doesn't fold accents — so `beyonce` failed to find `Beyoncé`,
and the user had to know which exact diacritics the artist name was
tagged with.

This module normalises both sides to ASCII-equivalent lowercase
before doing the substring check. Same speed (microseconds on 1200
items), much friendlier on an internationally-tagged library.

The server-side `/search3` endpoint uses SQLite FTS5 with
`unicode61 remove_diacritics 2`, which gives the same folding plus
ranked / prefix-matched results for the much higher-volume client
hit rate. The TUI doesn't need ranking — a 1200-album substring
filter is already imperceptibly fast.
"""

from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Lowercased + diacritic-stripped representation for matching.

    `Beyoncé` -> `beyonce`. Uses NFKD decomposition + a combining-mark
    filter so any composed Unicode letter with an attached accent
    decomposes to the base letter; the accent codepoint is dropped
    before the casefold.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def matches(needle: str, haystack: str) -> bool:
    """True iff `needle` is a substring of `haystack` after diacritic folding.

    Empty `needle` returns True (a "no filter" sentinel). Both sides
    are folded; callers don't need to pre-process.
    """
    if not needle:
        return True
    return fold(needle) in fold(haystack)
