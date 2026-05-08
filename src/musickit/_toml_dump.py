"""Tiny TOML writer for the shapes we actually serialize.

We only need to write back two kinds of TOML:

  - `<root>/.musickit/stars.toml` — `{"items": {<id>: <iso8601>, ...}}`
  - `~/.config/musickit/state.toml` — flat str→str pairs plus an optional
    `subsonic = {host, user, token, salt}` inline table.
  - `~/.config/musickit/musickit.toml` (during migration) — top-level
    sections each holding flat str/int/float/bool fields.

That's a narrow enough surface that the full `tomli_w` dependency was
overkill. This module covers exactly those shapes; if a caller hands us
something else (lists of tables, datetimes, nested-deeper-than-one
tables) `dump` raises `TypeError` with a clear message instead of
silently producing invalid TOML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# TOML 1.0.0 escape rules (https://toml.io/en/v1.0.0#string). We don't
# emit literal-strings or multiline forms — quoted basic strings cover
# every value we write.
_ESCAPE_TABLE = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape_str(s: str) -> str:
    """Render a Python str as a quoted TOML basic string."""
    out: list[str] = ['"']
    for ch in s:
        if ch in _ESCAPE_TABLE:
            out.append(_ESCAPE_TABLE[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _format_value(value: Any) -> str:
    """Render a Python scalar as its TOML literal form."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _escape_str(value)
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _is_bare_key(key: str) -> bool:
    """A bare key is letters/digits/_/- only, per TOML 1.0.0 §2.1."""
    return key != "" and all(c.isalnum() or c in "-_" for c in key)


def _format_key(key: str) -> str:
    """Render a TOML key, quoting it when not a bare key."""
    return key if _is_bare_key(key) else _escape_str(key)


def _format_inline_table(table: dict[str, Any]) -> str:
    """Render `{a = 1, b = "x"}` for one-level inline tables."""
    parts = [f"{_format_key(k)} = {_format_value(v)}" for k, v in table.items()]
    return "{" + ", ".join(parts) + "}"


def dumps(data: dict[str, Any]) -> str:
    """Serialize `data` to a TOML string.

    Supported shapes:
      - top-level: scalars + dicts
      - dict values become either inline tables (flat) or section headers
        (when they themselves contain dicts is NOT supported — TypeError)
      - dict-of-strings under `items` is treated as a section, not inline
        (used by stars.toml)

    Anything outside that grammar raises TypeError.
    """
    top_scalars: list[str] = []
    sections: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            # Heuristic: if the dict has nested dicts, give up — we don't
            # support multi-level tables. Otherwise it's a section.
            if any(isinstance(v, dict) for v in value.values()):
                raise TypeError(f"nested tables not supported (key {key!r})")
            sections.append(_format_section(key, value))
        else:
            top_scalars.append(f"{_format_key(key)} = {_format_value(value)}")
    out_parts: list[str] = []
    if top_scalars:
        out_parts.append("\n".join(top_scalars))
    out_parts.extend(sections)
    return ("\n\n".join(out_parts)).rstrip() + "\n"


def _format_section(name: str, table: dict[str, Any]) -> str:
    """Render `[name]` followed by `key = value` lines."""
    lines = [f"[{_format_key(name)}]"]
    for key, value in table.items():
        lines.append(f"{_format_key(key)} = {_format_value(value)}")
    return "\n".join(lines)


def dump_path(data: dict[str, Any], path: Path) -> None:
    """Write `data` to `path` as TOML. Creates parent dirs on demand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data), encoding="utf-8")
