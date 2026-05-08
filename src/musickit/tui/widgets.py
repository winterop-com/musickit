"""TUI widget classes + the shared color palette.

All widgets are pure presentation — they expose `reactive` attributes that
the App writes to. No widget reaches up into the App; the App pulls/pushes
state via `query_one(...)`.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input, ListItem, ListView, Static

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
        # All four icons must be reliably single-cell — picking from
        # Unicode's "Geometric Shapes" block (U+25xx) which has no emoji
        # presentation, so Apple Terminal / iTerm / Alacritty all render
        # them as 1-cell text. The previous ☺ (U+263A WHITE SMILING FACE)
        # was getting promoted to a 2-cell emoji glyph in modern
        # terminals, which pushed the "Artists" label one column right of
        # the others.
        rows = [
            f" [{C_ACCENT}]♪[/]  Tracks   [dim]{self.track_count:>5}[/]",
            f" [{C_ACCENT}]◉[/]  Albums   [dim]{self.album_count:>5}[/]",
            f" [{C_ACCENT}]◆[/]  Artists  [dim]{self.artist_count:>5}[/]",
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
    """48-band spectrum analyzer in classic VU style.

    Same green / yellow / red gradient across all bars, keyed off vertical
    position (red top → yellow mid → green bottom). Sub-cell vertical
    resolution via unicode partial blocks (`▁▂▃▄▅▆▇█`) on the top edge of
    each bar so amplitude changes animate smoothly.
    """

    DEFAULT_CSS = """
    Visualizer {
        width: 1fr;
        /* Share the main column with the tracklist proportionally
           instead of grabbing a fixed 12 lines: when the terminal is
           short, the tracklist needs room too. min-height keeps the bars
           legible; max-height stops it from ballooning on tall windows.

           The fullscreen-mode override (`Screen.fullscreen #visualizer`)
           lives in `MusickitApp.CSS`, NOT here. Textual applies a
           widget's own DEFAULT_CSS but does not reliably propagate
           descendant-combinator rules from one widget's stylesheet to
           another widget's selector match — `Screen.fullscreen
           Visualizer { ... }` declared here was getting overridden by
           the base `Visualizer { max-height: 14; }` in the cascade
           even with strictly higher specificity. Lifting the override
           into the app-level stylesheet (where Screen-class rules
           naturally live) fixes it. */
        height: 1fr;
        min-height: 6;
        max-height: 14;
        padding: 0 2;
        border: round $primary 30%;
    }
    """

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Spectrum"

    _PARTIAL_BLOCKS = "▁▂▃▄▅▆▇█"  # 1/8th increments

    levels = reactive([0.0] * 48)

    def render(self) -> str:
        rows = max(4, max(0, self.size.height - 1))
        red_cutoff = max(1, rows // 5)
        yellow_cutoff = red_cutoff + max(1, rows // 3)
        # Uniform bar width with a 1-cell gap between bars (classic VU
        # look). Leftover modulo cells get distributed across the gaps
        # so the meter fills the full content width — first `extra` gaps
        # are 2 cells, the rest are 1 cell. Falls back to gap=0 only if
        # avail is too narrow to fit even bar_width=1 with gaps.
        n_bars = len(self.levels) or 1
        avail = max(0, self.content_size.width)
        gaps_total = max(0, n_bars - 1)
        if avail >= n_bars + gaps_total:
            bar_width = (avail - gaps_total) // n_bars
            min_gap = 1
        else:
            bar_width = max(1, avail // n_bars)
            min_gap = 0
        used = bar_width * n_bars + min_gap * gaps_total
        leftover = max(0, avail - used)
        # `extra` gaps get an extra +1 cell so the meter is full-width.
        extra_gaps = leftover if gaps_total > 0 else 0
        empty_cell = " " * bar_width
        small_gap = " " * min_gap
        wide_gap = " " * (min_gap + 1)
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
            for i, level in enumerate(self.levels):
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
                if i < n_bars - 1:
                    line_parts.append(wide_gap if i < extra_gaps else small_gap)
            lines.append("".join(line_parts))
        return "\n".join(lines)


class LyricsPane(Static):
    """Synced lyrics pane — current line highlighted, played lines dimmed.

    Renders parsed LRC content. When `synced` is False (plain-text lyrics
    body), shows the lines without highlighting. The active-line index is
    derived from `position_ms`; the line whose `start_ms` is the largest
    value <= `position_ms` is treated as currently playing.

    Toggled via the `l` keybind in the App. Mutually exclusive with the
    visualizer in the same right-pane region — App's `Screen.show-lyrics`
    class hides the visualizer and shows this widget.
    """

    DEFAULT_CSS = """
    LyricsPane {
        width: 1fr;
        height: 1fr;
        min-height: 6;
        max-height: 14;
        padding: 0 2;
        border: round $primary 30%;
        display: none;
        overflow-y: auto;
    }
    LyricsPane.visible {
        display: block;
    }
    """

    # Actual content type is `list[LrcLine]` — declared as `list` here to
    # avoid pulling the dataclass into widgets.py and keep this file
    # dependency-light. App writes a list of LrcLine into it.
    lines: reactive[list] = reactive([], layout=True)
    position_ms: reactive[int] = reactive(0)
    synced: reactive[bool] = reactive(False)
    placeholder: reactive[str] = reactive("[dim]No lyrics for this track.[/]")

    def on_mount(self) -> None:  # noqa: D102
        self.border_title = "Lyrics"

    def watch_position_ms(self, _old: int, _new: int) -> None:  # noqa: D102
        # Re-render so the highlighted line tracks the position. Cheap —
        # render only walks `len(lines)`, which tops out at a few hundred.
        self.refresh()

    def render(self) -> str:
        rows = self.lines
        if not rows:
            return self.placeholder
        if not self.synced:
            return "\n".join(getattr(line, "text", str(line)) for line in rows)

        active = -1
        pos = self.position_ms
        for idx, line in enumerate(rows):
            if line.start_ms <= pos:
                active = idx
            else:
                break

        out: list[str] = []
        for idx, line in enumerate(rows):
            text = line.text or " "
            if idx == active:
                out.append(f"[bold {C_ACTIVE}]{text}[/]")
            elif idx < active:
                out.append(f"[dim]{text}[/]")
            else:
                out.append(text)
        return "\n".join(out)


class ProgressLine(Static):
    """`▶ mm:ss <bar> mm:ss` — click anywhere on the bar to seek."""

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

    # Layout offsets — keep in sync with `render()`. The bar starts after
    # the leading padding + icon (1 cell) + space (1) + pos label (5) +
    # 2-space separator. We also reserve `_TRAILING_RESERVE` cells after
    # the bar (2-space separator + dur label = 7) so clicks past the bar
    # don't land in the dur label and seek to the end accidentally.
    _PAD_LEFT = 2
    _BAR_OFFSET = 9  # icon + space + pos + 2-space separator
    _TRAILING_RESERVE = 7

    class Seek(Message):
        """Posted when the user clicks on the bar to seek."""

        def __init__(self, seconds: float) -> None:
            super().__init__()
            self.seconds = seconds

    async def _on_click(self, event: events.Click) -> None:  # noqa: PLW3201
        """Seek to the time corresponding to the clicked column."""
        if self.duration <= 0 or event.button != 1:
            return
        # Recompute bar geometry to match render(). content_size.width
        # already excludes padding; here `event.x` is widget-relative
        # (includes padding) so we add `_PAD_LEFT` to map into widget
        # coordinates.
        width = max(20, self.content_size.width - 16)
        bar_start = self._PAD_LEFT + self._BAR_OFFSET
        bar_end = bar_start + width
        x = event.x
        if x < bar_start or x >= bar_end:
            return
        ratio = (x - bar_start) / max(1, width - 1)
        ratio = max(0.0, min(1.0, ratio))
        self.post_message(self.Seek(ratio * self.duration))

    def render(self) -> str:
        # Layout: `<icon> <pos>  <bar>  <dur>`. Icon (1 cell) + space (1)
        # + pos (5) + 2 + bar + 2 + dur (5) = 16 cells of fixed content;
        # the bar fills the rest. `content_size.width` already excludes
        # padding so no double-subtract. Min width 20 for tiny terminals.
        width = max(20, self.content_size.width - 16)
        # Heavy block (`█`) for the filled portion + light shade (`░`) for
        # the unfilled — both are full-cell glyphs so the bar looks like a
        # continuous line edge-to-edge instead of a thin filled stub
        # followed by an invisible single-pixel rule.
        if self.duration <= 0:
            bar = f"[{C_MUTED}]{'░' * width}[/]"
        else:
            ratio = max(0.0, min(1.0, self.position / self.duration))
            filled = int(round(ratio * width))
            bar = f"[{C_TIME}]{'█' * filled}[/][{C_MUTED}]{'░' * (width - filled)}[/]"
        # Compact state icon at the line head. Always visible, no
        # right-edge clipping risk like the previous trailing `[playing]`
        # badge had on narrow widget widths.
        if self.state == "playing":
            icon = f"[{C_PLAYING}]▶[/]"
        elif self.state == "paused":
            icon = f"[{C_PAUSED}]‖[/]"
        else:
            icon = f"[{C_DIM}]■[/]"
        pos = fmt_mmss(self.position)
        dur = fmt_mmss(self.duration)
        return f"{icon} [{C_TIME}]{pos}[/]  {bar}  [{C_TIME}]{dur}[/]"


class TrackTableHeader(Static):
    """Column headers for the track table (`#  Title  Artist  Time`).

    Title column stretches to fill the available width; the # / Artist /
    Time columns stay fixed at the same widths used in `format_track_row`
    so the header lines up with the rows below it.
    """

    DEFAULT_CSS = """
    TrackTableHeader {
        width: 1fr;
        height: 2;
        padding: 0 2;
    }
    """

    def render(self) -> str:
        from musickit.tui.formatters import ARTIST_WIDTH, TIME_WIDTH, compute_title_width

        rule_width = max(20, self.size.width - 4)
        rule = "─" * rule_width
        # The header has 4 cells of horizontal padding; tracklist rows
        # have 2 (their own padding only). The title column is the same
        # in both, so this matches up.
        title_width = compute_title_width(self.size.width, header_padding=4)
        return (
            f"[{C_HEADER}]{'#':>3} {'Title':<{title_width + 2}}{'Artist':<{ARTIST_WIDTH}} {'Time':>{TIME_WIDTH}}[/]\n"
            f"[dim]{rule}[/]"
        )


class TrackList(ListView):
    """Focusable playlist (column-aligned rows).

    Zebra striping (alternate row backgrounds) makes scanning a long
    album easier; the highlighted row is brightened further so the
    cursor position stands out from the stripes. The currently-playing
    track gets the warm `C_ACTIVE` color in its label (set by
    `format_track_row`) — the visual difference between "where I am"
    (cursor) and "what's playing" (orange marker) is intentional.

    Click semantics: single click moves the cursor only (no playback).
    Double click within ~400ms plays the track. Mirrors Spotify /
    iTunes / etc. — and lets the user click a row to edit its tags
    via `e` without restarting whatever's currently playing.
    """

    DEFAULT_CSS = """
    TrackList {
        padding: 0 1;
        /* Fill the parent VerticalScroll container; the scroll container
           itself gets a 2fr share of the main column (see App CSS). */
        height: 1fr;
    }
    TrackList > ListItem {
        height: 1;
    }
    TrackList > ListItem:even {
        background: $boost 40%;
    }
    TrackList > ListItem.-highlight {
        background: $primary 50%;
        text-style: bold;
    }
    """

    _DOUBLE_CLICK_WINDOW_S = 0.4

    class FocusLost(Message):
        """Posted when the TrackList loses focus.

        App listens to snap the cursor back to the currently-playing track.
        """

    def _on_blur(self, event: events.Blur) -> None:  # noqa: PLW3201
        """Surface a `FocusLost` message; App listens to snap the cursor."""
        del event
        self.post_message(self.FocusLost())

    def _on_list_item__child_clicked(self, event: ListItem._ChildClicked) -> None:  # noqa: PLW3201
        """Override Textual's default click → Selected behaviour.

        Default: any click on a row posts `Selected` (== Enter), which the
        App treats as "play this track." We want single click to just move
        the cursor; only a second click within the double-click window
        actually plays.

        IMPORTANT: Textual dispatches a message to a handler in EVERY class
        in the MRO that defines one, not just the most-derived. Without
        `event.prevent_default()` here, our override runs AND the parent
        ListView's handler also runs (which posts Selected unconditionally
        → plays the track). `prevent_default()` sets `_no_default_action`
        and breaks the dispatch loop in `MessagePump._get_dispatch_methods`.
        """
        import time
        from typing import cast

        event.prevent_default()
        event.stop()
        item = event.item
        idx = -1
        for i, child in enumerate(self.children):
            if child is item:
                idx = i
                break
        if idx < 0:
            return
        last_idx: int = getattr(self, "_last_click_idx", -1)
        last_time: float = getattr(self, "_last_click_time", 0.0)
        now = time.monotonic()
        is_double = last_idx == idx and (now - last_time) < self._DOUBLE_CLICK_WINDOW_S
        self._last_click_idx = idx
        self._last_click_time = now
        # Cast: pyright narrows `ListView.index` to `int` after this
        # assignment, breaking external `tracklist.index = None` calls.
        self.index = cast("int | None", idx)
        self.focus()
        if is_double:
            self.post_message(self.Selected(self, item, idx))


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
    BrowserList > ListItem.-highlight {
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
        # Curated quick-reference. The full binding list lives in
        # Textual's HelpPanel (toggled via `?`); KeyBar covers the verbs
        # used most often during playback.
        items = [
            ("space", "Play"),
            ("←/→", "Nav"),
            ("</>", "Seek"),
            ("9/0", "Vol"),
            ("n", "Next"),
            ("/", "Filter"),
            ("e", "Edit"),
            ("a", "AirPlay"),
            ("g", "Mix"),
            ("l", "Lyrics"),
            ("v", "Viz"),
            ("f", "Full"),
            ("?", "Help"),
            ("q", "Quit"),
        ]
        # All-dim. The keys themselves are slightly less dim than labels
        # so a quick scan can still find a binding.
        return "  ".join(f"[dim]{key}[/][dim]·[/][dim]{label}[/]" for key, label in items)
