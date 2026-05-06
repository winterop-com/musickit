"""TUI `/`-bar filter — diacritic-folded multi-token AND substring matching.

Real-world libraries are full of accented characters (Sigur Rós,
Björk, José González, Beyoncé, Daft Punk's Cœur). The folding step
NFKD-decomposes both sides and drops combining marks so `beyonce`
finds `Beyoncé` without the user typing the exact diacritics.

Multiple whitespace-split tokens AND together: `daft homework`
matches `Daft Punk - Homework` because each token is independently a
folded substring of the haystack. Pure-substring would fail there
since the literal `"daft homework"` is never adjacent in the row.

The server-side `/search3` endpoint uses SQLite FTS5 with
`unicode61 remove_diacritics 2` for ranked / prefix-matched results
on the much higher-volume client hit rate. The TUI doesn't need
ranking — it just hides non-matching rows, and per-pane substring
on 1200 items is microseconds.
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
    """True iff every whitespace-split token in `needle` is a substring of `haystack`.

    Both sides are diacritic-folded before the substring check. Empty
    `needle` returns True (a "no filter" sentinel). A whitespace-only
    needle (e.g. `"   "`) also returns True for the same reason.

    Multi-token semantics:
      `matches("daft homework", "Daft Punk - Homework")` -> True
      `matches("daft music",    "Daft Punk - Homework")` -> False
    """
    if not needle or not needle.strip():
        return True
    folded_haystack = fold(haystack)
    return all(tok in folded_haystack for tok in fold(needle).split())
