"""Pure row-formatter functions for the TrackList — track + station rows.

Column widths are dynamic: callers pass `title_width` so the title cell
expands to fill the available pane width. Other columns (#, artist,
time) stay fixed so the header in `TrackTableHeader` lines up with the
rows below it. The visible rule that makes long titles look "clipped"
is that f-string `:<N` padding counts markup characters (`[bold ...][/]`)
along with the visible text, so we pad the visible string FIRST and
then wrap it in markup — that way the padded width matches what the
user sees on screen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from musickit.tui.widgets import C_ACTIVE, fmt_mmss

if TYPE_CHECKING:
    from musickit.library import LibraryAlbum, LibraryTrack
    from musickit.radio import RadioStation


# Fixed-width columns. Title is dynamic; everything else stays the
# same so `TrackTableHeader` and the rows below line up.
ARTIST_WIDTH = 26
TIME_WIDTH = 6
DEFAULT_TITLE_WIDTH = 44
# Non-title cells in a row: ` ▶ ` + `#` (3) + 1 space + 1 marker + 1 space
# + ARTIST_WIDTH + 1 space + TIME_WIDTH = 3 + 1 + 1 + 1 + 26 + 1 + 6 = 39 cells.
# Subtract from the widget's content area to get the title's slot.
_NON_TITLE_CELLS = 3 + 1 + 1 + 1 + ARTIST_WIDTH + 1 + TIME_WIDTH


def compute_title_width(widget_width: int, *, header_padding: int = 2) -> int:
    """Title column width that fills the rest of the row.

    `widget_width`: the widget's `self.size.width` (Textual cells).
    `header_padding`: 2 for TrackList (CSS `padding: 0 1`), 4 for
    TrackTableHeader (CSS `padding: 0 2`). Matters because Textual's
    padding is INSIDE the widget rect, eating from `size.width`.
    """
    avail = max(0, widget_width - header_padding)
    return max(20, avail - _NON_TITLE_CELLS)


def _pad(value: str, width: int) -> str:
    """Truncate to `width`, then left-pad with spaces to exactly `width`."""
    return value[:width].ljust(width)


def format_track_row(
    idx: int,
    track: LibraryTrack,
    album: LibraryAlbum,
    *,
    marker: bool,
    title_width: int = DEFAULT_TITLE_WIDTH,
) -> str:
    """Render one library track row. `marker=True` gets the ▶ glyph + warm accent.

    When the track has `starred` set (an ISO timestamp from the server's
    `StarStore.enrich`), a ♥ glyph occupies the marker slot — but the
    `▶` for the currently-playing track wins when both apply, so the
    eye still tracks playback first. This is a deliberate "minimum
    visible" decision; a dedicated star column would shift every
    header offset, so we reuse the existing single-char marker slot.
    """
    if marker:
        glyph = f"[bold {C_ACTIVE}]▶[/]"
    elif track.starred:
        glyph = f"[{C_ACTIVE}]♥[/]"
    else:
        glyph = " "
    title_padded = _pad(track.title or track.path.stem, title_width)
    artist_padded = _pad(track.artist or album.artist_dir, ARTIST_WIDTH)
    time_str = fmt_mmss(track.duration_s) if track.duration_s > 0 else "  —  "
    if marker:
        num = f"[{C_ACTIVE}]{idx + 1:>3}[/]"
        artist_cell = f"[{C_ACTIVE}]{artist_padded}[/]"
        title_cell = f"[bold {C_ACTIVE}]{title_padded}[/]"
    else:
        num = f"[dim]{idx + 1:>3}[/]"
        artist_cell = f"[dim]{artist_padded}[/]"
        title_cell = title_padded
    return f"{num} {glyph} {title_cell}{artist_cell} [dim]{time_str:>{TIME_WIDTH}}[/]"


def format_station_row(
    idx: int,
    station: RadioStation,
    *,
    marker: bool,
    title_width: int = DEFAULT_TITLE_WIDTH,
) -> str:
    """Render one radio station row. `marker=True` gets the ▶ glyph + warm accent."""
    glyph = f"[bold {C_ACTIVE}]▶[/]" if marker else " "
    name_padded = _pad(station.name, title_width)
    desc_padded = _pad(station.description or "Live", ARTIST_WIDTH)
    if marker:
        num = f"[{C_ACTIVE}]{idx + 1:>3}[/]"
        name_cell = f"[bold {C_ACTIVE}]{name_padded}[/]"
        desc_cell = f"[{C_ACTIVE}]{desc_padded}[/]"
    else:
        num = f"[dim]{idx + 1:>3}[/]"
        name_cell = name_padded
        desc_cell = f"[dim]{desc_padded}[/]"
    return f"{num} {glyph} {name_cell}{desc_cell} [dim]{'LIVE':>{TIME_WIDTH}}[/]"
