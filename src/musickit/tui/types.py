"""Shared TUI types + tree-sizing constants + small string helpers."""

from __future__ import annotations

from enum import Enum

_TREE_DEFAULT_WIDTH = 32
_TREE_MIN_WIDTH = 20
_TREE_MAX_WIDTH = 80
_TREE_RESIZE_STEP = 4
# Total non-name cells in a browser row, accounting for:
#   - sidebar `padding: 0 1`             → 2 cells
#   - BrowserList `border: round`        → 2 cells
#   - BrowserList `padding: 0 1`         → 2 cells
#   - row decoration ` ▸ ` + `  (NN)`    → ~8 cells (allowing 3-digit counts)
# = 14 cells of chrome before/after the name. Used in `_fit_sidebar_width`
# to size the sidebar so the longest name fits without overflow-clipping.
_BROWSER_DECORATION_PAD = 14


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
