"""Shared TUI types + tree-sizing constants + small string helpers."""

from __future__ import annotations

from enum import Enum

_TREE_DEFAULT_WIDTH = 32
_TREE_MIN_WIDTH = 20
_TREE_MAX_WIDTH = 80
_TREE_RESIZE_STEP = 4
# Decoration overhead per browser row: ` ▸ ` prefix + ` (NN)` suffix + padding ≈ 8 cells.
_BROWSER_DECORATION_PAD = 8


class RepeatMode(str, Enum):
    """Cycle target for the `r` keybinding."""

    OFF = "Off"
    ALBUM = "Album"
    TRACK = "Track"


def _truncate(value: str, max_len: int) -> str:
    """Cap `value` at `max_len` cells with an ellipsis. Used by browser rows."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"
