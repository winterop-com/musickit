"""Lyrics: parse `[mm:ss.xx]` LRC, fetch from LRCLIB, read/write `.lrc` sidecars."""

from __future__ import annotations

import re
from dataclasses import dataclass

from musickit.lyrics.lrclib import LrcLibClient, LrcLibError
from musickit.lyrics.sidecar import read_sidecar, sidecar_path, write_sidecar

__all__ = [
    "LrcLibClient",
    "LrcLibError",
    "LrcLine",
    "is_synced",
    "parse_lrc",
    "read_sidecar",
    "sidecar_path",
    "write_sidecar",
]


# `[mm:ss.xx]` (fractional) and `[mm:ss]` (no-fraction). Captures minutes,
# seconds, and an optional fraction. The fraction is treated as decimal —
# 1 digit = 100ms, 2 digits = 10ms each, 3 digits = 1ms each — matching the
# common LRC dialects in the wild.
_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
# Metadata header tags — `[ar: Artist]`, `[ti: Title]`, `[length: 03:42]`,
# etc. Look like timestamps but are not. Filter them so they don't render
# as "00:01.23 ar: Artist" lines.
_META_HEADER_RE = re.compile(r"^\[[a-zA-Z]+:.*\]$")


@dataclass(frozen=True, slots=True)
class LrcLine:
    """One synced LRC line: timestamp in milliseconds + visible text."""

    start_ms: int
    text: str


def parse_lrc(text: str) -> list[LrcLine]:
    r"""Parse LRC `[mm:ss.xx] line` format. Plain lines get start_ms=0.

    Tolerant of: no-fraction timestamps `[01:23]`, multi-timestamp
    lines `[00:01.00][00:05.00] same text` (each timestamp produces a
    line), and metadata headers `[ar: Artist]` (silently dropped).

    Returns an empty list for empty input. Each tagged line yields one
    `LrcLine`; un-tagged lines yield a single `LrcLine(start_ms=0, ...)`
    so callers that just want plain text can `\n`-join `.text`.
    """
    if not text:
        return []
    out: list[LrcLine] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if _META_HEADER_RE.match(line):
            continue
        timestamps = list(_TIMESTAMP_RE.finditer(line))
        if not timestamps:
            # Plain text — keep it so unsynced LRC bodies still render.
            out.append(LrcLine(start_ms=0, text=line))
            continue
        # Strip every leading timestamp before the first non-timestamp char.
        last_end = 0
        for m in timestamps:
            if m.start() != last_end:
                # Timestamp not contiguous with the start — treat the
                # line as plain text from `last_end` onward.
                break
            last_end = m.end()
        body = line[last_end:].strip()
        for m in timestamps:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_raw = m.group(3) or "0"
            # Normalise fraction to 3-digit ms. `5` → `500`, `50` → `500`,
            # `500` → `500`. Right-pad with zeros so column count matches.
            frac_ms = int(frac_raw.ljust(3, "0")[:3])
            start_ms = mins * 60_000 + secs * 1_000 + frac_ms
            out.append(LrcLine(start_ms=start_ms, text=body))
    return out


def is_synced(lines: list[LrcLine]) -> bool:
    """True if any line has a non-zero timestamp — i.e. real LRC, not plain."""
    return any(line.start_ms > 0 for line in lines)
