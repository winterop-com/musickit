"""TUI widget classes + the shared color palette.

All widgets are pure presentation — they expose `reactive` attributes that
the App writes to. No widget reaches up into the App; the App pulls/pushes
state via `query_one(...)`.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Input, ListView, Static

# ---------------------------------------------------------------------------
# Palette (ncmpcpp-leaning: cyan headers, green meters, dim grey rules,
# yellow=warning state, red=peaks). One central place so themes stay tight.
# ---------------------------------------------------------------------------

C_HEADER = "cyan"
C_LABEL = "#7aa2f7"  # softer blue for "Artist:" / "Title:" labels
C_PLAYING = "#9ece6a"  # green for "playing" state + meter fill
C_PAUSED = "#e0af68"  # warm amber for paused
C_PEAK = "#f7768e"  # peak / red zone in the visualizer
C_WARM = "#e0af68"  # mid zone (yellow / amber)
C_ACCENT = "#bb9af7"  # accent (e.g. "Favorites" style highlights)
# Warm orange used SPARINGLY — currently-playing track marker, now-playing
# title. Sets the active track apart from the cyan/green palette used
# elsewhere for navigation/structural elements.
C_ACTIVE = "#ff9e64"
C_DIM = "#3a3a3a"
C_MUTED = "#565f89"
C_TIME = "#7aa2f7"


def fmt_mmss(seconds: float) -> str:
    """`123.4` → `02:03`. Used by ProgressLine, StatusBar, and the App."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class FilterInput(Input):
    """One-line filter bar mounted above the browser or tracklist.

    Overrides the inherited Enter binding with `priority=True` so Enter is
    fully consumed by the input (posts `Input.Submitted`) and never bubbles
    up to the App's Enter binding (which would trigger track selection on
    the focused list — defeating the whole filter UX).
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    FilterInput {
        height: 1;
        border: none;
        padding: 0 1;
        background: $boost;
    }
    """


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class TopBar(Static):
    """Centered app title at the very top."""

    DEFAULT_CSS = """
    TopBar {
        height: 1;
        padding: 0 1;
        content-align: center middle;
        background: $boost;
    }
    """

    def render(self) -> str:
        from musickit import __version__

        return f"[bold cyan]musickit[/] [dim]v{__version__}[/]"


class SidebarStats(Static):
    """`Library` category list: counts of tracks / albums / artists / folders."""

    DEFAULT_CSS = """
    SidebarStats {
        height: auto;
        padding: 0 1;
        border: round $primary 30%;
    }
    """

    track_count = reactive(0)
    album_count = reactive(0)
    artist_count = reactive(0)
    folder_count = reactive(0)

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Library"

    def render(self) -> str:
        rows = [
            f" [{C_ACCENT}]♪[/]  Tracks   [dim]{self.track_count:>5}[/]",
            f" [{C_ACCENT}]◉[/]  Albums   [dim]{self.album_count:>5}[/]",
            f" [{C_ACCENT}]☺[/]  Artists  [dim]{self.artist_count:>5}[/]",
            f" [{C_ACCENT}]▦[/]  Folders  [dim]{self.folder_count:>5}[/]",
        ]
        return "\n".join(rows)


class NowPlayingMeta(Static):
    """Right-side metadata grid: Artist / Title / Album / Year / Genre / Format."""

    DEFAULT_CSS = """
    NowPlayingMeta {
        width: 1fr;
        height: auto;
        padding: 0 2;
        border: round $primary 30%;
    }
    """

    artist = reactive("—")
    title_text = reactive("—")
    album = reactive("—")
    year = reactive("—")
    genre = reactive("—")
    fmt = reactive("—")

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Now Playing"

    def render(self) -> str:
        # Title + Artist get strong emphasis (bold + accent); secondary
        # fields stay quieter so the eye lands on what's playing first.
        rows = [
            f"[bold {C_ACTIVE}]{self.title_text}[/]",
            f"[bold {C_LABEL}]{self.artist}[/]  [dim]·  {self.album}[/]",
            f"[{C_LABEL}]Year:[/]    [dim]{self.year}[/]",
            f"[{C_LABEL}]Genre:[/]   [dim]{self.genre}[/]",
            f"[{C_LABEL}]Format:[/]  [dim]{self.fmt}[/]",
        ]
        return "\n".join(rows)


class Visualizer(Static):
    """24-band spectrum analyzer in classic VU style.

    Same green / yellow / red gradient across all bars, keyed off vertical
    position (red top → yellow mid → green bottom). Sub-cell vertical
    resolution via unicode partial blocks (`▁▂▃▄▅▆▇█`) on the top edge of
    each bar so amplitude changes animate smoothly.
    """

    DEFAULT_CSS = """
    Visualizer {
        height: 12;
        padding: 0 2;
        border: round $primary 30%;
    }
    Screen.fullscreen Visualizer {
        height: 1fr;
    }
    """

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Levels"

    _PARTIAL_BLOCKS = "▁▂▃▄▅▆▇█"  # 1/8th increments

    levels = reactive([0.0] * 24)

    def render(self) -> str:
        rows = max(4, max(0, self.size.height - 1))
        red_cutoff = max(1, rows // 5)
        yellow_cutoff = red_cutoff + max(1, rows // 3)
        # Spread bars across the full available width. Each bar gets
        # `bar_width` block chars + 1 cell gap. Floor at 3 so it still looks
        # like a meter on narrow terminals.
        n_bars = len(self.levels) or 1
        avail = max(0, self.size.width - 4)  # account for the widget's padding
        bar_width = max(3, (avail - n_bars) // n_bars)
        empty_cell = " " * bar_width
        lines: list[str] = []
        for row_idx in range(rows):
            if row_idx < red_cutoff:
                color = C_PEAK
            elif row_idx < yellow_cutoff:
                color = C_WARM
            else:
                color = C_PLAYING
            row_top = 1.0 - row_idx / rows
            row_bottom = 1.0 - (row_idx + 1) / rows
            line_parts: list[str] = []
            for level in self.levels:
                if level >= row_top:
                    line_parts.append(f"[{color}]{'█' * bar_width}[/]")
                elif level > row_bottom:
                    fraction = (level - row_bottom) / max(1e-6, row_top - row_bottom)
                    block = self._PARTIAL_BLOCKS[
                        min(len(self._PARTIAL_BLOCKS) - 1, int(fraction * len(self._PARTIAL_BLOCKS)))
                    ]
                    line_parts.append(f"[{color}]{block * bar_width}[/]")
                else:
                    line_parts.append(empty_cell)
                line_parts.append(" ")
            lines.append("".join(line_parts))
        return "\n".join(lines)


class ProgressLine(Static):
    """`mm:ss [▰▰▰▰░░░░] mm:ss   [playing]` bar."""

    DEFAULT_CSS = """
    ProgressLine {
        width: 1fr;
        height: 1;
        padding: 0 2;
    }
    """

    position = reactive(0.0)
    duration = reactive(0.0)
    state = reactive("stopped")  # "playing" | "paused" | "stopped"

    def render(self) -> str:
        width = max(20, self.size.width - 30)
        # `C_MUTED` (slate) for the unfilled track instead of `C_DIM`
        # (#3a3a3a, nearly invisible against the dark background).
        if self.duration <= 0:
            bar = f"[{C_MUTED}]{'─' * width}[/]"
        else:
            ratio = max(0.0, min(1.0, self.position / self.duration))
            filled = int(round(ratio * width))
            bar = f"[{C_TIME}]{'━' * filled}[/][{C_MUTED}]{'─' * (width - filled)}[/]"
        if self.state == "playing":
            badge = f"[{C_PLAYING}][playing][/]"
        elif self.state == "paused":
            badge = f"[{C_PAUSED}][paused][/]"
        else:
            badge = "[dim][stopped][/]"
        pos = fmt_mmss(self.position)
        dur = fmt_mmss(self.duration)
        return f"[{C_TIME}]{pos}[/]  {bar}  [{C_TIME}]{dur}[/]   {badge}"


class TrackTableHeader(Static):
    """Column headers for the track table (`#  Title  Artist  Time`)."""

    DEFAULT_CSS = """
    TrackTableHeader {
        width: 1fr;
        height: 2;
        padding: 0 2;
    }
    """

    def render(self) -> str:
        # `padding: 0 2` → 2 cells reserved on each side.
        rule_width = max(20, self.size.width - 4)
        rule = "─" * rule_width
        return f"[{C_HEADER}]{'#':>3}  {'Title':<46}{'Artist':<28}{'Time':>6}[/]\n[dim]{rule}[/]"


class TrackList(ListView):
    """Focusable playlist (column-aligned rows).

    Zebra striping (alternate row backgrounds) makes scanning a long
    album easier; the highlighted row is brightened further so the
    cursor position stands out from the stripes. The currently-playing
    track gets the warm `C_ACTIVE` color in its label (set by
    `format_track_row`) — the visual difference between "where I am"
    (cursor) and "what's playing" (orange marker) is intentional.
    """

    DEFAULT_CSS = """
    TrackList {
        padding: 0 1;
        height: 1fr;
    }
    TrackList > ListItem {
        height: 1;
    }
    TrackList > ListItem:even {
        background: $boost 40%;
    }
    TrackList > ListItem.--highlight {
        background: $primary 50%;
        text-style: bold;
    }
    """


class BrowserList(ListView):
    """Flat-list directory browser. Enter on a row drills in or goes up.

    Replaces the older Tree-based navigator. Two levels deep:
      - root: list of artist dirs
      - inside an artist: a `..` entry + that artist's album dirs
    Selecting an album row hands focus to the playlist (right column)
    so Enter on a track plays it immediately.
    """

    DEFAULT_CSS = """
    BrowserList {
        height: 1fr;
        padding: 0 1;
        overflow-x: hidden;
        border: round $primary 30%;
    }
    BrowserList > ListItem {
        height: 1;
    }
    BrowserList > ListItem.--highlight {
        background: $primary 30%;
    }
    """

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Browse"


class BrowserInfo(Static):
    """Detail panel below the browser. Shows audit warnings for the highlighted album."""

    DEFAULT_CSS = """
    BrowserInfo {
        height: auto;
        max-height: 12;
        padding: 0 1;
        border: round $primary 30%;
    }
    """

    # `layout=True` so the panel re-measures its auto height when `body`
    # grows from the placeholder (1 line) to a multi-line warning list.
    # Without this, only the first line is visible — the per-warning
    # bullets get clipped.
    body = reactive("", layout=True)

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Info"

    def render(self) -> str:
        return self.body or "[dim]Highlight an album to see audit warnings.[/]"


class ScanOverlay(Static):
    """Full-screen scanning view shown while the library is being indexed.

    When `Screen.scanning` is set, the rest of the UI is hidden via CSS in
    the App and this widget fills the screen with a centered "Scanning…"
    card. Avoids the awkward half-empty body during scan.
    """

    DEFAULT_CSS = """
    ScanOverlay {
        width: 100%;
        height: 100%;
        align: center middle;
        content-align: center middle;
        text-align: center;
        background: $background;
        display: none;
    }
    ScanOverlay.visible {
        display: block;
    }
    """

    body = reactive("[bold cyan]Scanning library…[/]")

    def render(self) -> str:
        return self.body


class StatusBar(Static):
    """Bottom single-line status: Vol / Repeat / Shuffle / Time."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 2;
        background: $boost;
    }
    """

    volume = reactive(100)
    repeat = reactive("Off")
    shuffle = reactive("Off")
    position = reactive(0.0)
    duration = reactive(0.0)
    album_label = reactive("—")
    cursor_label = reactive("0/0")

    def render(self) -> str:
        vol_filled = int(round(self.volume / 100.0 * 12))
        vol_bar = f"[{C_PLAYING}]{'|' * vol_filled}[/][{C_DIM}]{'-' * (12 - vol_filled)}[/]"
        repeat_color = C_PLAYING if self.repeat != "Off" else C_DIM
        shuffle_color = C_PLAYING if self.shuffle != "Off" else C_DIM
        time_str = f"{fmt_mmss(self.position)} / {fmt_mmss(self.duration)}"
        # `\\[…\\]` to render literal brackets around the volume bar in cliamp/
        # ncmpcpp style, without confusing Rich's tag parser.
        return (
            f"[{C_LABEL}]Vol:[/] [{C_PLAYING}]{self.volume}%[/] \\[{vol_bar}\\]    "
            f"[{C_LABEL}]Repeat:[/] [{repeat_color}]{self.repeat.lower()}[/]    "
            f"[{C_LABEL}]Shuffle:[/] [{shuffle_color}]{self.shuffle.lower()}[/]    "
            f"[{C_LABEL}]Album:[/] [{C_ACCENT}]{self.album_label}[/] [dim]({self.cursor_label})[/]"
            f"    [{C_LABEL}]Time:[/] [{C_TIME}]{time_str}[/]"
        )


class KeyBar(Static):
    """Bottom keybinding hint bar (ncmpcpp-style numbered shortcuts).

    Visually muted so the eye lands on `StatusBar` (playback state)
    first; the keybindings are reference info, not active controls.
    """

    DEFAULT_CSS = """
    KeyBar {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def render(self) -> str:
        items = [
            ("space", "Play"),
            ("enter", "Open"),
            ("←/→", "Nav"),
            ("</>", "Seek"),
            ("n", "Next"),
            ("p", "Prev"),
            ("s", "Shuffle"),
            ("r", "Repeat"),
            ("f", "Fullscreen"),
            ("^r", "Rescan"),
            ("tab", "Focus"),
            ("^←/→", "Resize"),
            ("q", "Quit"),
        ]
        # All-dim. The keys themselves are slightly less dim than labels
        # so a quick scan can still find a binding.
        return "  ".join(f"[dim]{key}[/][dim]·[/][dim]{label}[/]" for key, label in items)
