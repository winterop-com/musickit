"""Filename-shape parsers — the rescue path for tagless / scene-vandalised rips."""

from __future__ import annotations

import re
from pathlib import Path

_SCENE_TAG_SUFFIX_RE = re.compile(r"[\s\-_]+(?:atm|lzy|dqm|tfm|rjk|atb|wre|cmc|mfa)$", re.IGNORECASE)


def _humanise_slug(s: str) -> str:
    """Clean a snake_case filename slug into Title Case.

    Real-world rips frequently store track titles only in the filename, in
    `lowercase_underscore-separated_form-scene` style (e.g. Absolute Music's
    `miio_feat_daddy_boastin_-_nar_vi_tva_blir_en-atm`). This function:
    - drops a trailing scene-tag suffix (`-atm`, `-lzy`, `-dqm`, …)
    - converts underscores to spaces
    - title-cases each word (preserving apostrophes that `str.title()` mangles)
    Idempotent on already-humanised strings.
    """
    if not s:
        return s
    cleaned = _SCENE_TAG_SUFFIX_RE.sub("", s)
    if "_" not in cleaned:
        # Already looks human (no slug separators) — leave it.
        return cleaned.strip()
    cleaned = cleaned.replace("_", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # `.capitalize()` is gentler on apostrophes than `.title()` (it leaves
    # "don't" alone instead of producing "Don'T").
    return " ".join(w.capitalize() if w else w for w in cleaned.split(" "))


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    match = re.match(r"^\s*\d{1,3}\s*[.\-_]+\s*(.+)$", stem)
    body = match.group(1) if match else stem
    return _humanise_slug(body.strip())


def _parse_filename_for_va(path: Path) -> tuple[str | None, str | None]:
    """Parse `NN - [VA - ]Artist - Title` filenames common on VA rips.

    Returns `(artist, title)` if the filename has at least 3 ` - ` segments
    after the track number, otherwise `(None, None)` and the caller falls
    back to `_title_from_filename`. Strips a leading `VA -` segment if present.
    Underscored slugs are humanised (`per_gessle_-_tycker_om` →
    `Per Gessle - Tycker Om`) before splitting, so the dash detector works
    on the human form.
    """
    stem = path.stem
    body_match = re.match(r"^\s*\d{1,3}\s*[.\-_]+\s*(.+)$", stem)
    body = body_match.group(1) if body_match else stem
    body = _humanise_slug(body)
    parts = [p.strip() for p in re.split(r"\s+-\s+", body) if p.strip()]
    if parts and parts[0].lower() in ("va", "various", "various artists"):
        parts = parts[1:]
    if len(parts) < 2:
        return None, None
    artist = " - ".join(parts[:-1])
    title = parts[-1]
    return artist, title


def _track_no_from_filename(path: Path) -> int | None:
    match = re.match(r"^\s*(\d{1,3})", path.stem)
    return int(match.group(1)) if match else None
