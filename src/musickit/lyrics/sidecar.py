"""`.lrc` sidecar IO — atomic write next to the audio file."""

from __future__ import annotations

import os
from pathlib import Path


def sidecar_path(track_path: Path) -> Path:
    """Return `<track>.lrc` next to the audio file (preserves the original suffix)."""
    return track_path.with_suffix(track_path.suffix + ".lrc")


def read_sidecar(track_path: Path) -> str | None:
    """Read `<track>.lrc`. Returns its UTF-8 text, or None if missing / unreadable."""
    p = sidecar_path(track_path)
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def write_sidecar(track_path: Path, text: str) -> None:
    """Atomically write `<track>.lrc`. Replaces any existing sidecar.

    Uses temp-file + `os.replace` so a crash mid-write can't leave the
    sidecar half-written. UTF-8, no BOM. Skipped silently if `text` is
    empty (a sidecar that says nothing is worse than no sidecar).
    """
    if not text:
        return
    p = sidecar_path(track_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        # Best-effort temp cleanup so a write-failure doesn't litter
        # the library with `.lrc.tmp` files.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover — read-only mount
            pass
        raise
