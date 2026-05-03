"""Textual app: ncmpcpp-styled library + now-playing + spectrum visualizer."""

from __future__ import annotations

import logging
import random
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import ListItem, ListView, Static

from musickit import library as library_mod
from musickit import radio as radio_mod
from musickit.tui.player import AudioPlayer

if TYPE_CHECKING:
    from musickit.library import LibraryAlbum, LibraryIndex, LibraryTrack
    from musickit.radio import RadioStation

log = logging.getLogger(__name__)


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
C_DIM = "#3a3a3a"
C_MUTED = "#565f89"
C_TIME = "#7aa2f7"


class RepeatMode(str, Enum):
    """Cycle target for the `r` keybinding."""

    OFF = "Off"
    ALBUM = "Album"
    TRACK = "Track"


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
        return "[bold cyan]musickit[/]"


class SidebarStats(Static):
    """`Library` category list: counts of tracks / albums / artists / folders."""

    DEFAULT_CSS = """
    SidebarStats {
        height: auto;
        padding: 1 1;
    }
    """

    track_count = reactive(0)
    album_count = reactive(0)
    artist_count = reactive(0)
    folder_count = reactive(0)

    def render(self) -> str:
        rows = [
            f"[{C_HEADER}]Library[/]",
            "[dim]──────────[/]",
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
        height: auto;
        padding: 1 2;
    }
    """

    artist = reactive("—")
    title_text = reactive("—")
    album = reactive("—")
    year = reactive("—")
    genre = reactive("—")
    fmt = reactive("—")

    def render(self) -> str:
        rows = [
            f"[{C_HEADER}]Now Playing[/]",
            "[dim]──────────────────────────────────────────────[/]",
            f"[{C_LABEL}]Artist:[/]  {self.artist}",
            f"[{C_LABEL}]Title:[/]   [bold]{self.title_text}[/]",
            f"[{C_LABEL}]Album:[/]   {self.album}",
            f"[{C_LABEL}]Year:[/]    {self.year}",
            f"[{C_LABEL}]Genre:[/]   {self.genre}",
            f"[{C_LABEL}]Format:[/]  {self.fmt}",
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
        height: 6;
        padding: 0 2;
    }
    Screen.fullscreen Visualizer {
        height: 1fr;
    }
    """

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
        height: 1;
        padding: 0 2;
    }
    """

    position = reactive(0.0)
    duration = reactive(0.0)
    state = reactive("stopped")  # "playing" | "paused" | "stopped"

    def render(self) -> str:
        width = max(20, self.size.width - 30)
        if self.duration <= 0:
            bar = f"[{C_DIM}]{'─' * width}[/]"
        else:
            ratio = max(0.0, min(1.0, self.position / self.duration))
            filled = int(round(ratio * width))
            bar = f"[{C_TIME}]{'━' * filled}[/][{C_DIM}]{'─' * (width - filled)}[/]"
        if self.state == "playing":
            badge = f"[{C_PLAYING}][playing][/]"
        elif self.state == "paused":
            badge = f"[{C_PAUSED}][paused][/]"
        else:
            badge = "[dim][stopped][/]"
        pos = _fmt_mmss(self.position)
        dur = _fmt_mmss(self.duration)
        return f"[{C_TIME}]{pos}[/]  {bar}  [{C_TIME}]{dur}[/]   {badge}"


class TrackTableHeader(Static):
    """Column headers for the track table (`#  Title  Artist  Time`)."""

    DEFAULT_CSS = """
    TrackTableHeader {
        height: 2;
        padding: 0 2;
    }
    """

    def render(self) -> str:
        return f"[{C_HEADER}]{'#':>3}  {'Title':<46}{'Artist':<28}{'Time':>6}[/]\n[dim]{'─' * 90}[/]"


class TrackList(ListView):
    """Focusable playlist (column-aligned rows)."""

    DEFAULT_CSS = """
    TrackList {
        padding: 0 1;
        height: 1fr;
    }
    TrackList > ListItem.--highlight {
        background: $primary 30%;
    }
    """


class BrowserHeader(Static):
    """Path header above the browser list (`Browse` or `Browse · <Artist>`)."""

    DEFAULT_CSS = """
    BrowserHeader {
        height: 2;
        padding: 1 1 0 1;
    }
    """

    path = reactive("Browse")

    def render(self) -> str:
        return f"[{C_HEADER}]{self.path}[/]\n[dim]──────────[/]"


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
    }
    BrowserList > ListItem {
        height: 1;
    }
    BrowserList > ListItem.--highlight {
        background: $primary 30%;
    }
    """


class BrowserInfo(Static):
    """Detail panel below the browser. Shows audit warnings for the highlighted album."""

    DEFAULT_CSS = """
    BrowserInfo {
        height: auto;
        max-height: 8;
        padding: 1 1;
        background: $boost;
    }
    """

    body = reactive("")

    def render(self) -> str:
        return self.body or "[dim]Highlight an album to see audit warnings.[/]"


class ScanOverlay(Static):
    """Centered overlay shown during the initial library scan / rescans.

    Sits on top of the body via Textual's `layer:` CSS feature so it doesn't
    require recomposing the layout each time. Hidden when there's no scan in
    flight.
    """

    DEFAULT_CSS = """
    ScanOverlay {
        /* Card-style modal: not full width. `layer: overlay` keeps it above
           the body without a relayout when shown/hidden. */
        layer: overlay;
        offset: 50% 30%;
        margin: 0 -30;  /* shift back by half-width so 50% lands on center */
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
        text-align: center;
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
        time_str = f"{_fmt_mmss(self.position)} / {_fmt_mmss(self.duration)}"
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
    """Bottom keybinding hint bar (ncmpcpp-style numbered shortcuts)."""

    DEFAULT_CSS = """
    KeyBar {
        height: 1;
        padding: 0 2;
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
        return "  ".join(f"[bold]{key}[/] [dim]{label}[/]" for key, label in items)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


_TREE_DEFAULT_WIDTH = 32
_TREE_MIN_WIDTH = 20
_TREE_MAX_WIDTH = 80
_TREE_RESIZE_STEP = 4
# Decoration overhead per browser row: ` ▸ ` prefix + ` (NN)` suffix + padding ≈ 8 cells.
_BROWSER_DECORATION_PAD = 8


class MusickitApp(App[None]):
    """ncmpcpp-styled three-row Textual app."""

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 32; border-right: tall $primary 30%; }
    #main { width: 1fr; }
    #now-playing-row { height: auto; }
    /* Fullscreen: keep the visualizer + now-playing visible, hide everything
       library-related. Visualizer height is bumped to 1fr in
       `action_toggle_fullscreen`. */
    Screen.fullscreen #sidebar { display: none; }
    Screen.fullscreen #track-header { display: none; }
    Screen.fullscreen #track-scroll { display: none; }
    Screen.fullscreen #status { display: none; }
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", show=False),
        Binding("space", "toggle_pause", "Play/Pause", show=False),
        Binding("enter", "play_selected", "Play", show=False),
        Binding("n", "next_track", "Next", show=False),
        Binding("p", "prev_track", "Prev", show=False),
        Binding("plus,equals_sign", "vol_up", "Vol+", show=False),
        Binding("minus", "vol_down", "Vol-", show=False),
        # `←` / `→` are context-aware (see `action_left` / `action_right`):
        # they navigate between panes when one is focused, and only fall back
        # to seek when nothing's focused. Use `<` / `>` for always-on seek
        # (Shift+`,`/`.` — the "arrow" shifted variants of those keys).
        Binding("left", "left", "Left", show=False),
        Binding("right", "right", "Right", show=False),
        Binding("less_than_sign", "seek_back", "Seek -", show=False),
        Binding("greater_than_sign", "seek_fwd", "Seek +", show=False),
        Binding("s", "toggle_shuffle", "Shuffle", show=False),
        Binding("r", "cycle_repeat", "Repeat", show=False),
        Binding("f", "toggle_fullscreen", "Fullscreen", show=False),
        Binding("tab", "focus_next", "Focus", show=False),
        Binding("ctrl+left", "tree_narrower", "Tree-", show=False),
        Binding("ctrl+right", "tree_wider", "Tree+", show=False),
        Binding("backspace", "browser_up", "Up", show=False),
        Binding("ctrl+r,f5", "rescan_library", "Rescan", show=False),
        Binding("question_mark", "toggle_keybar", "Help", show=False),
    ]

    def __init__(self, root: Path | None) -> None:
        super().__init__()
        self._root: Path | None = root
        self._index: LibraryIndex | None = None
        self._player = AudioPlayer()
        self._player.on_track_end = self._on_track_end
        self._player.on_track_failed = self._on_track_failed
        self._player.on_metadata_change = self._on_stream_metadata_change
        self._current_album: LibraryAlbum | None = None
        self._current_track_idx: int | None = None
        self._marker_idx: int | None = None
        self._shuffle = False
        # Radio: curated streaming stations loaded from `~/.config/musickit/radio.toml`.
        # When the user selects "Radio" in the browser, the right-side track
        # list is populated with these (each station is a streaming "track")
        # and the "current album" is the synthetic `_RADIO_VIEW`.
        self._radio_stations: list[RadioStation] = []
        self._in_radio_view: bool = False
        # Set by AudioPlayer.on_metadata_change (worker thread). Drained in
        # the UI tick so the `Now Playing` block updates with new ICY title.
        self._stream_metadata_dirty: bool = False
        self._repeat = RepeatMode.OFF
        self._end_pending = False
        # Browser navigation state: None = at top level (artists);
        # a string = drilled into that artist's albums.
        self._browse_artist: str | None = None

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield SidebarStats(id="stats")
                yield BrowserHeader(id="browser-header")
                yield BrowserList(id="browser")
                yield BrowserInfo(id="browser-info")
            with Vertical(id="main"):
                with Horizontal(id="now-playing-row"):
                    yield NowPlayingMeta(id="meta")
                yield Visualizer(id="visualizer")
                yield ProgressLine(id="progress")
                yield TrackTableHeader(id="track-header")
                with VerticalScroll(id="track-scroll", can_focus=False):
                    yield TrackList(id="tracklist")
        yield StatusBar(id="status")
        yield KeyBar(id="keybar")
        yield ScanOverlay(id="scan-overlay")

    def on_mount(self) -> None:
        self.title = "musickit"
        # Visualizer animates at ~30 FPS so bars feel responsive to the audio.
        # Other status (time, progress, meta) only needs ~4 FPS — saves redraw
        # work since the surrounding text doesn't change frame-to-frame.
        self.set_interval(1 / 30, self._refresh_visualizer)
        self.set_interval(0.25, self._refresh_status)
        self.set_interval(0.05, self._drain_end_pending)
        self.set_interval(0.5, self._drain_stream_metadata)
        # Seed the user's `radio.toml` with starter stations on first run, then
        # load. Cheap (no network).
        try:
            radio_mod.seed_default_config()
        except OSError:  # pragma: no cover — read-only home, etc.
            pass
        self._radio_stations = radio_mod.load_stations()
        if self._root is None:
            # Radio-only launch: skip the library scan, render the browser
            # immediately with just the Radio entry, and drop straight into
            # the station list on the right.
            self._populate_sidebar_stats()
            self._populate_browser()
            self._open_radio_view()
            return
        # Kick off the initial scan in a worker thread so the TUI is
        # responsive immediately. The overlay covers the body until the
        # first index lands.
        self._show_scan_overlay("[bold cyan]Scanning library…[/]")
        self._scan_library_async(initial=True)

    # ------------------------------------------------------------------
    # Sidebar / tree population
    # ------------------------------------------------------------------

    def _populate_sidebar_stats(self) -> None:
        if self._index is None:
            return
        stats = self.query_one(SidebarStats)
        artists = {a.artist_dir for a in self._index.albums}
        stats.track_count = sum(a.track_count for a in self._index.albums)
        stats.album_count = len(self._index.albums)
        stats.artist_count = len(artists)
        stats.folder_count = len(self._index.albums)  # one folder per album

    def _populate_browser(self) -> None:
        browser = self.query_one(BrowserList)
        header = self.query_one(BrowserHeader)
        # Invalidate the cursor BEFORE mutating children. Otherwise a stale
        # index from the prior list (e.g. row 32 of a long album list) can
        # leak into the new list (3-item artists view) and crash the next
        # `↑`/`↓` keypress with an IndexError. `None` is the empty-list state.
        browser.index = None
        browser.clear()
        if self._index is None or not self._index.albums:
            # Radio-only mode (or empty library): browser shows just the
            # Radio entry. Selecting it fills the right pane with stations.
            header.path = "Browse"
            radio_item = ListItem(Static(f" [{C_ACCENT}]📻[/] [bold]Radio[/]  [dim]({len(self._radio_stations)})[/]"))
            radio_item.entry_kind = "radio"  # type: ignore[attr-defined]
            radio_item.entry_data = None  # type: ignore[attr-defined]
            browser.append(radio_item)
            if self._root is not None:
                # Library was specified but had nothing. Surface that.
                browser.append(ListItem(Static("[dim](no albums)[/]")))
            self._fit_sidebar_width()
            self.call_after_refresh(self._set_browser_cursor, 0)
            return
        if self._browse_artist is None:
            header.path = "Browse"
            self._populate_browser_artists(browser)
        else:
            header.path = f"Browse · [bold]{self._browse_artist}[/]"
            self._populate_browser_albums(browser, self._browse_artist)
        self._fit_sidebar_width()
        # Defer cursor placement until after the children have actually
        # mounted. Setting `index` on a list whose children haven't been
        # rendered yet doesn't paint the highlight reliably.
        target_index = 0
        if self._browse_artist is not None and len(browser.children) > 1:
            target_index = 1
        self.call_after_refresh(self._set_browser_cursor, target_index)

    def _set_browser_cursor(self, target_index: int) -> None:
        """Place the cursor on `target_index` after children mount.

        Also makes sure focus stays on the browser (Textual sometimes drops
        focus when a widget's children get mass-replaced) and refreshes the
        info panel manually — `index =` assignments don't always fire
        `ListView.Highlighted` after a `clear()`+`append()` cycle.
        """
        browser = self.query_one(BrowserList)
        if target_index >= len(browser.children):
            return
        browser.index = target_index
        if not browser.has_focus:
            browser.focus()
        # Force an info-panel update for the new highlight.
        item = browser.children[target_index]
        self._update_browser_info(item if isinstance(item, ListItem) else None)

    def _update_browser_info(self, item: ListItem | None) -> None:
        info = self.query_one(BrowserInfo)
        if item is None:
            info.body = ""
            return
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind == "radio":
            n = len(self._radio_stations)
            info.body = f"[{C_LABEL}]Radio[/]\n[dim]{n} curated station(s)[/]"
        elif kind == "album" and isinstance(data, library_mod.LibraryAlbum):
            if data.warnings:
                lines = [f"[{C_PEAK}]⚠ {len(data.warnings)} warning(s)[/]"]
                for w in data.warnings:
                    lines.append(f"[{C_WARM}]·[/] {w}")
                info.body = "\n".join(lines)
            else:
                info.body = f"[{C_PLAYING}]✓ no warnings[/]"
        elif kind == "artist" and isinstance(data, str):
            assert self._index is not None
            albums = [a for a in self._index.albums if a.artist_dir == data]
            flagged = sum(1 for a in albums if a.warnings)
            if flagged:
                info.body = f"[{C_LABEL}]{data}[/]\n[dim]{len(albums)} album(s)[/] [{C_PEAK}]· {flagged} flagged[/]"
            else:
                info.body = f"[{C_LABEL}]{data}[/]\n[dim]{len(albums)} album(s)[/]"
        else:
            info.body = ""

    def _fit_sidebar_width(self) -> None:
        """Size the sidebar to fit the longest visible browser entry, capped at the max."""
        if self._index is None:
            return
        if self._browse_artist is None:
            longest = max((len(a.artist_dir) for a in self._index.albums), default=0)
        else:
            longest = max(
                (len(a.album_dir) for a in self._index.albums if a.artist_dir == self._browse_artist),
                default=0,
            )
        # Also factor in the sidebar-stats labels — they shouldn't get truncated.
        target = max(_TREE_DEFAULT_WIDTH, longest + _BROWSER_DECORATION_PAD)
        target = min(_TREE_MAX_WIDTH, max(_TREE_MIN_WIDTH, target))
        sidebar = self.query_one("#sidebar")
        sidebar.styles.width = target

    def _populate_browser_artists(self, browser: BrowserList) -> None:
        assert self._index is not None
        # Radio sits at the very top — single click drops you into the
        # station list (right pane), bypassing the artist→album drill.
        radio_item = ListItem(Static(f" [{C_ACCENT}]📻[/] [bold]Radio[/]  [dim]({len(self._radio_stations)})[/]"))
        radio_item.entry_kind = "radio"  # type: ignore[attr-defined]
        radio_item.entry_data = None  # type: ignore[attr-defined]
        browser.append(radio_item)
        by_artist: dict[str, list[LibraryAlbum]] = {}
        for album in self._index.albums:
            by_artist.setdefault(album.artist_dir, []).append(album)
        max_name = _TREE_MAX_WIDTH - _BROWSER_DECORATION_PAD
        for artist in sorted(by_artist, key=str.lower):
            count = len(by_artist[artist])
            name = _truncate(artist, max_name)
            label = f" [{C_ACCENT}]▸[/] {name}  [dim]({count})[/]"
            item = ListItem(Static(label))
            item.entry_kind = "artist"  # type: ignore[attr-defined]
            item.entry_data = artist  # type: ignore[attr-defined]
            browser.append(item)

    def _populate_browser_albums(self, browser: BrowserList, artist: str) -> None:
        assert self._index is not None
        # `..` to go back up.
        up_item = ListItem(Static(f" [{C_ACCENT}]..[/]  [dim]Back[/]"))
        up_item.entry_kind = "up"  # type: ignore[attr-defined]
        up_item.entry_data = None  # type: ignore[attr-defined]
        browser.append(up_item)
        artist_albums = sorted(
            (a for a in self._index.albums if a.artist_dir == artist),
            key=lambda a: a.album_dir.lower(),
        )
        max_name = _TREE_MAX_WIDTH - _BROWSER_DECORATION_PAD
        for album in artist_albums:
            warn = f" [{C_PEAK}]⚠[/]" if album.warnings else ""
            name = _truncate(album.album_dir, max_name)
            label = f" [{C_ACCENT}]♪[/] {name}{warn}"
            item = ListItem(Static(label))
            item.entry_kind = "album"  # type: ignore[attr-defined]
            item.entry_data = album  # type: ignore[attr-defined]
            browser.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dispatch by which ListView fired the event."""
        browser = self.query_one(BrowserList)
        tracklist = self.query_one(TrackList)
        if event.list_view is browser:
            self._handle_browser_selection(event.item)
        elif event.list_view is tracklist:
            station = getattr(event.item, "station", None)
            if isinstance(station, radio_mod.RadioStation):
                self._play_station(station)
                return
            idx = getattr(event.item, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Show audit warnings for the highlighted album in the info panel."""
        browser = self.query_one(BrowserList)
        if event.list_view is not browser:
            return
        info = self.query_one(BrowserInfo)
        item = event.item
        if item is None:
            info.body = ""
            return
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind == "radio":
            n = len(self._radio_stations)
            info.body = f"[{C_LABEL}]Radio[/]\n[dim]{n} curated station(s)[/]"
        elif kind == "album" and isinstance(data, library_mod.LibraryAlbum):
            if data.warnings:
                lines = [f"[{C_PEAK}]⚠ {len(data.warnings)} warning(s)[/]"]
                for w in data.warnings:
                    lines.append(f"[{C_WARM}]·[/] {w}")
                info.body = "\n".join(lines)
            else:
                info.body = f"[{C_PLAYING}]✓ no warnings[/]"
        elif kind == "artist" and isinstance(data, str):
            assert self._index is not None
            albums = [a for a in self._index.albums if a.artist_dir == data]
            flagged = sum(1 for a in albums if a.warnings)
            if flagged:
                info.body = f"[{C_LABEL}]{data}[/]\n[dim]{len(albums)} album(s)[/] [{C_PEAK}]· {flagged} flagged[/]"
            else:
                info.body = f"[{C_LABEL}]{data}[/]\n[dim]{len(albums)} album(s)[/]"
        else:
            info.body = ""

    def _handle_browser_selection(self, item: ListItem) -> None:  # noqa: D102 — see body
        kind_first = getattr(item, "entry_kind", None)
        if kind_first == "radio":
            self._open_radio_view()
            self.query_one(TrackList).focus()
            return
        return self._handle_browser_selection_default(item)

    def _open_radio_view(self) -> None:
        """Populate the right-pane track list with the curated radio stations.

        Treated as a virtual album: `_in_radio_view=True` flips the playlist
        repopulation to render `RadioStation` rows instead of `LibraryTrack`s.
        Entering this view doesn't drill the browser — it stays where it is.
        """
        self._in_radio_view = True
        self._current_album = None
        self._current_track_idx = None
        self._marker_idx = None
        self._repopulate_radio_playlist()

    def _handle_browser_selection_default(self, item: ListItem) -> None:
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind == "up":
            self._browse_artist = None
            self._populate_browser()
        elif kind == "artist" and isinstance(data, str):
            self._browse_artist = data
            self._populate_browser()
        elif kind == "album" and isinstance(data, library_mod.LibraryAlbum):
            self._in_radio_view = False
            self._set_current_album(data, track_idx=None)
            self._repopulate_playlist()
            self.query_one(TrackList).focus()

    # ------------------------------------------------------------------
    # Playlist rendering
    # ------------------------------------------------------------------

    def _set_current_album(self, album: LibraryAlbum, *, track_idx: int | None) -> None:
        self._current_album = album
        self._current_track_idx = track_idx

    def _repopulate_playlist(self) -> None:
        tracklist = self.query_one(TrackList)
        # Same pattern as the browser: invalidate the cursor BEFORE clearing
        # children, then defer the new cursor placement via
        # `call_after_refresh` so it lands after the new ListItems mount.
        # Without the defer, the second album in a row often shows no
        # highlighted track because the previous album's children are still
        # mid-unmount when the synchronous `index =` runs.
        tracklist.index = None
        tracklist.clear()
        if self._current_album is None:
            self._marker_idx = None
            return
        album = self._current_album
        for i, track in enumerate(album.tracks):
            label = self._format_track_row(i, track, album, marker=(i == self._current_track_idx))
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            tracklist.append(item)
        self._marker_idx = self._current_track_idx
        if self._current_track_idx is not None and 0 <= self._current_track_idx < len(album.tracks):
            target = self._current_track_idx
        elif album.tracks:
            target = 0
        else:
            return
        self.call_after_refresh(self._set_tracklist_cursor, target)

    def _set_tracklist_cursor(self, target: int) -> None:
        tracklist = self.query_one(TrackList)
        if 0 <= target < len(tracklist.children):
            tracklist.index = target

    def _repopulate_radio_playlist(self) -> None:
        """Render the curated radio stations as the right-side track list.

        Each row carries a `RadioStation` instance via `item.station` so the
        track-list selection handler can dispatch to `_play_station` instead
        of the regular library-track path.
        """
        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        if not self._radio_stations:
            empty = ListItem(Static("[dim]No stations configured. Edit `~/.config/musickit/radio.toml`.[/]"))
            tracklist.append(empty)
            return
        for i, station in enumerate(self._radio_stations):
            label = self._format_station_row(i, station, marker=False)
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            item.station = station  # type: ignore[attr-defined]
            tracklist.append(item)
        self.call_after_refresh(self._set_tracklist_cursor, 0)

    def _format_station_row(self, idx: int, station: RadioStation, *, marker: bool) -> str:
        glyph = f"[bold {C_PLAYING}]▶[/]" if marker else " "
        name = station.name[:44]
        desc = (station.description or "Live")[:26]
        if marker:
            num = f"[{C_PLAYING}]{idx + 1:>3}[/]"
            name_cell = f"[bold]{name}[/]"
            desc_cell = f"[{C_PLAYING}]{desc}[/]"
        else:
            num = f"[dim]{idx + 1:>3}[/]"
            name_cell = name
            desc_cell = f"[dim]{desc}[/]"
        return f"{num} {glyph} {name_cell:<44}{desc_cell:<26} [dim]{'LIVE':>6}[/]"

    def _play_station(self, station: RadioStation) -> None:
        """Start an internet-radio stream and mark the row as playing."""
        idx = self._radio_stations.index(station) if station in self._radio_stations else None
        # `_marker_idx` follows the playing row's index; refresh both old + new.
        prev = self._marker_idx
        self._current_track_idx = idx
        self._marker_idx = idx
        self._player.play(station.url)
        # Repaint just the affected rows.
        if prev is not None and prev != idx:
            try:
                self.query_one(f"#track-row-{prev}", Static).update(
                    self._format_station_row(prev, self._radio_stations[prev], marker=False)
                )
            except Exception:  # pragma: no cover
                pass
        if idx is not None:
            try:
                self.query_one(f"#track-row-{idx}", Static).update(self._format_station_row(idx, station, marker=True))
            except Exception:  # pragma: no cover
                pass

    def _on_stream_metadata_change(self) -> None:
        """Player callback (off-thread) — flag the UI tick to redraw the title."""
        self._stream_metadata_dirty = True

    def _drain_stream_metadata(self) -> None:
        """UI tick: pull fresh ICY title into the now-playing meta if the player set it."""
        if not self._stream_metadata_dirty:
            return
        self._stream_metadata_dirty = False
        # The status refresh path below already reads stream_title; just nudge it.
        self._refresh_status()

    def _refresh_play_marker(self) -> None:
        if self._current_album is None:
            return
        album = self._current_album
        old, new = self._marker_idx, self._current_track_idx
        if old == new:
            return
        if old is not None and 0 <= old < len(album.tracks):
            self._update_track_row_label(old, marker=False)
        if new is not None and 0 <= new < len(album.tracks):
            self._update_track_row_label(new, marker=True)
        self._marker_idx = new

    def _update_track_row_label(self, idx: int, *, marker: bool) -> None:
        if self._current_album is None:
            return
        try:
            label_widget = self.query_one(f"#track-row-{idx}", Static)
        except Exception:
            return
        track = self._current_album.tracks[idx]
        label_widget.update(self._format_track_row(idx, track, self._current_album, marker=marker))

    def _format_track_row(self, idx: int, track: LibraryTrack, album: LibraryAlbum, *, marker: bool) -> str:
        glyph = f"[bold {C_PLAYING}]▶[/]" if marker else " "
        artist = (track.artist or album.artist_dir)[:26]
        title = (track.title or track.path.stem)[:44]
        time_str = _fmt_mmss(track.duration_s) if track.duration_s > 0 else "  —  "
        if marker:
            num = f"[{C_PLAYING}]{idx + 1:>3}[/]"
            artist_cell = f"[{C_PLAYING}]{artist}[/]"
            title_cell = f"[bold]{title}[/]"
        else:
            num = f"[dim]{idx + 1:>3}[/]"
            artist_cell = artist
            title_cell = title
        return f"{num} {glyph} {title_cell:<44}{artist_cell:<26} [dim]{time_str:>6}[/]"

    # ------------------------------------------------------------------
    # Status refresh
    # ------------------------------------------------------------------

    def _refresh_visualizer(self) -> None:
        """High-FPS visualizer tick — runs the FFT off the audio thread."""
        self._player.update_band_levels()
        self.query_one(Visualizer).levels = self._player.band_levels

    def _refresh_status(self) -> None:
        meta = self.query_one(NowPlayingMeta)
        progress = self.query_one(ProgressLine)
        status = self.query_one(StatusBar)
        if self._player.is_live:
            self._populate_meta_from_stream(meta)
            progress.position = 0.0
            progress.duration = 0.0
            progress.state = "playing" if self._player.is_playing else "paused" if self._player.is_paused else "stopped"
        else:
            track = self._currently_playing_track()
            if track is None:
                meta.title_text = "—"
                meta.artist = "—"
                meta.album = "—"
                meta.year = "—"
                meta.genre = "—"
                meta.fmt = "—"
                progress.position = 0.0
                progress.duration = 0.0
                progress.state = "stopped"
            else:
                meta.title_text = track.title or track.path.stem
                meta.artist = track.artist or self._current_album.artist_dir if self._current_album else "—"
                meta.album = self._current_album.album_dir if self._current_album else "—"
                meta.year = track.year or "—"
                meta.genre = "—"  # not tracked in light scan
                meta.fmt = track.path.suffix.lstrip(".").upper()
                progress.position = self._player.position
                progress.duration = self._player.duration
                if self._player.is_paused:
                    progress.state = "paused"
                elif self._player.is_playing:
                    progress.state = "playing"
                else:
                    progress.state = "stopped"
        status.volume = self._player.volume
        status.repeat = self._repeat.value
        status.shuffle = "On" if self._shuffle else "Off"
        status.position = self._player.position
        status.duration = self._player.duration
        if self._in_radio_view:
            status.album_label = "Radio"
            cur = (self._current_track_idx or 0) + 1 if self._current_track_idx is not None else 0
            status.cursor_label = f"{cur}/{len(self._radio_stations)}"
        elif self._current_album is not None:
            status.album_label = self._current_album.album_dir
            cur = (self._current_track_idx or 0) + 1 if self._current_track_idx is not None else 0
            status.cursor_label = f"{cur}/{len(self._current_album.tracks)}"
        else:
            status.album_label = "—"
            status.cursor_label = "0/0"

    def _populate_meta_from_stream(self, meta: NowPlayingMeta) -> None:
        """Now-playing meta when a live stream is active.

        Title comes from ICY `StreamTitle` (current song), Album from
        `icy-name` (station). When `StreamTitle` looks like `Artist - Title`,
        we split it for the Artist/Title fields.
        """
        station = self._player.stream_station_name or "Live Stream"
        raw = self._player.stream_title or ""
        artist = "—"
        title = raw or station
        if " - " in raw:
            split_artist, split_title = raw.split(" - ", 1)
            artist = split_artist.strip() or "—"
            title = split_title.strip() or station
        meta.artist = artist
        meta.title_text = title
        meta.album = station
        meta.year = "—"
        meta.genre = "—"
        meta.fmt = "STREAM"

    def _drain_end_pending(self) -> None:
        if self._end_pending:
            self._end_pending = False
            self._advance_track()

    def _currently_playing_track(self) -> LibraryTrack | None:
        if self._current_album is None or self._current_track_idx is None:
            return None
        if 0 <= self._current_track_idx < len(self._current_album.tracks):
            return self._current_album.tracks[self._current_track_idx]
        return None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_play_selected(self) -> None:
        tracklist = self.query_one(TrackList)
        browser = self.query_one(BrowserList)
        focused = self.focused
        # Enter inside the playlist plays the highlighted track.
        if focused is tracklist and tracklist.highlighted_child is not None:
            idx = getattr(tracklist.highlighted_child, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()
                return
        # Enter inside the browser drills in / goes up — see `on_list_view_selected`
        # which fires for `Selected` events; this catches keyboard activation
        # when ListView doesn't fire `Selected` (e.g. deselected highlight).
        if focused is browser and browser.highlighted_child is not None:
            self._handle_browser_selection(browser.highlighted_child)
            return
        # Fallback: if an album is loaded, just play from the current cursor.
        if self._current_album is None:
            return
        if self._current_track_idx is None:
            self._current_track_idx = 0
        self._play_current()

    def action_toggle_pause(self) -> None:
        self._player.toggle_pause()

    def action_next_track(self) -> None:
        self._advance_track(force=True)

    def action_prev_track(self) -> None:
        if self._current_album is None or self._current_track_idx is None:
            return
        new_idx = max(0, self._current_track_idx - 1)
        self._current_track_idx = new_idx
        self._play_current()

    def action_seek_fwd(self) -> None:
        self._player.seek(self._player.position + 5.0)

    def action_seek_back(self) -> None:
        self._player.seek(max(0.0, self._player.position - 5.0))

    def action_left(self) -> None:
        """←: navigation only — never seek.

        Seek lives on `<` / `>` (always-on, focus-independent). Mixing seek
        into ←/→ caused audible jumps when a track was playing — right after
        Enter on an album the focus moves to TrackList, and the old fallback
        would seek every right arrow.
        """
        focused = self.focused
        if isinstance(focused, BrowserList):
            if self._browse_artist is not None:
                self._pop_browser_one_level()
            return
        if isinstance(focused, TrackList):
            self.query_one(BrowserList).focus()

    def _pop_browser_one_level(self) -> None:
        """Go from an artist's album list back to the artist list.

        Restores the cursor onto the artist we just exited, which feels much
        better than always snapping to row 0 of the artists list.
        """
        prior_artist = self._browse_artist
        self._browse_artist = None
        self._populate_browser()
        if prior_artist is None or self._index is None:
            return
        # Compute the row index from the data model (artist names sorted
        # case-insensitively, same key `_populate_browser_artists` uses) —
        # NOT from `browser.children`. Right after a clear()+append() cycle,
        # `browser.children` may still report the OLD items, so an index
        # derived from it would be wrong by the time the deferred cursor
        # set actually runs.
        artist_names = sorted({a.artist_dir for a in self._index.albums}, key=str.lower)
        try:
            prior_idx = artist_names.index(prior_artist)
        except ValueError:
            return
        # `_populate_browser` already scheduled `_set_browser_cursor(0)`.
        # Schedule another with the prior-artist index — `call_after_refresh`
        # is FIFO so the later schedule wins.
        self.call_after_refresh(self._set_browser_cursor, prior_idx)

    def action_right(self) -> None:
        """→: drill into the browser only — never seek (use `>`)."""
        focused = self.focused
        if isinstance(focused, BrowserList) and focused.highlighted_child is not None:
            self._handle_browser_selection(focused.highlighted_child)

    def action_vol_up(self) -> None:
        self._player.set_volume(min(100, self._player.volume + 5))

    def action_vol_down(self) -> None:
        self._player.set_volume(max(0, self._player.volume - 5))

    def action_toggle_shuffle(self) -> None:
        self._shuffle = not self._shuffle

    def action_cycle_repeat(self) -> None:
        order = [RepeatMode.OFF, RepeatMode.ALBUM, RepeatMode.TRACK]
        self._repeat = order[(order.index(self._repeat) + 1) % len(order)]

    def action_tree_wider(self) -> None:
        self._resize_sidebar(_TREE_RESIZE_STEP)

    def action_tree_narrower(self) -> None:
        self._resize_sidebar(-_TREE_RESIZE_STEP)

    def action_browser_up(self) -> None:
        """Backspace: pop one level in the browser if drilled into an artist."""
        if self._browse_artist is not None:
            self._pop_browser_one_level()

    def action_rescan_library(self) -> None:
        """Re-walk the library root and refresh the browser.

        There's no on-disk DB — the index is built fresh on `on_mount` and
        kept in memory. Use this binding (Ctrl+R / F5) when you've moved
        files around outside the TUI and want the changes reflected. In
        radio-only mode (no library), reload the radio config instead.
        """
        if self._root is None:
            self._radio_stations = radio_mod.load_stations()
            self._populate_browser()
            if self._in_radio_view:
                self._repopulate_radio_playlist()
            return
        self._show_scan_overlay("[bold cyan]Rescanning library…[/]")
        self._scan_library_async(initial=False)

    # ------------------------------------------------------------------
    # Async library scan
    # ------------------------------------------------------------------

    def _show_scan_overlay(self, body: str) -> None:
        overlay = self.query_one(ScanOverlay)
        overlay.body = body
        overlay.add_class("visible")

    def _hide_scan_overlay(self) -> None:
        overlay = self.query_one(ScanOverlay)
        overlay.remove_class("visible")

    @work(thread=True, exclusive=True, group="scan")
    def _scan_library_async(self, *, initial: bool) -> None:
        """Run `library.scan` in a worker thread; route progress + result back."""
        if self._root is None:
            return
        prior_artist = self._browse_artist
        root = self._root

        def on_album(album_dir: Path, idx: int, total: int) -> None:
            self.call_from_thread(self._on_scan_progress, album_dir, idx, total)

        new_index = library_mod.scan(root, on_album=on_album)
        library_mod.audit(new_index)
        self.call_from_thread(self._on_scan_complete, new_index, prior_artist, initial)

    def _on_scan_progress(self, album_dir: Path, idx: int, total: int) -> None:
        overlay = self.query_one(ScanOverlay)
        name = album_dir.name
        if len(name) > 50:
            name = name[:49] + "…"
        bar_width = 30
        ratio = idx / max(1, total)
        filled = int(round(ratio * bar_width))
        bar = f"[{C_PLAYING}]{'█' * filled}[/][{C_DIM}]{'░' * (bar_width - filled)}[/]"
        overlay.body = f"[bold cyan]Scanning library…[/]\n\n{bar}\n[dim]{idx} / {total}[/]\n\n[dim]{name}[/]"

    def _on_scan_complete(self, new_index: LibraryIndex, prior_artist: str | None, initial: bool) -> None:
        del initial  # current behaviour is the same for first-mount and rescan
        self._index = new_index
        if prior_artist is not None and not any(a.artist_dir == prior_artist for a in self._index.albums):
            self._browse_artist = None
        self._populate_sidebar_stats()
        self._populate_browser()
        self._hide_scan_overlay()

    def _resize_sidebar(self, delta: int) -> None:
        sidebar = self.query_one("#sidebar")
        current = sidebar.styles.width
        try:
            current_cells = int(current.value) if current is not None else _TREE_DEFAULT_WIDTH
        except (TypeError, ValueError):
            current_cells = _TREE_DEFAULT_WIDTH
        new_width = max(_TREE_MIN_WIDTH, min(_TREE_MAX_WIDTH, current_cells + delta))
        sidebar.styles.width = new_width

    def action_toggle_fullscreen(self) -> None:
        if self.screen.has_class("fullscreen"):
            self.screen.remove_class("fullscreen")
            self.query_one(Visualizer).styles.height = 6
        else:
            self.screen.add_class("fullscreen")
            self.query_one(Visualizer).styles.height = "1fr"

    def action_toggle_keybar(self) -> None:
        """`?` shows / hides the bottom keybindings hint bar."""
        keybar = self.query_one(KeyBar)
        keybar.styles.display = "none" if keybar.styles.display != "none" else "block"

    # ------------------------------------------------------------------
    # Playback orchestration
    # ------------------------------------------------------------------

    def _play_current(self) -> None:
        track = self._currently_playing_track()
        if track is None:
            return
        self._player.play(track.path)
        self._refresh_play_marker()

    def _advance_track(self, *, force: bool = False) -> None:
        if self._current_album is None or self._current_track_idx is None:
            return
        if not force and self._repeat is RepeatMode.TRACK:
            self._play_current()
            return
        if self._shuffle:
            n = len(self._current_album.tracks)
            if n <= 1:
                self._player.stop()
                return
            choices = [i for i in range(n) if i != self._current_track_idx]
            self._current_track_idx = random.choice(choices)
            self._play_current()
            return
        next_idx = self._current_track_idx + 1
        if next_idx < len(self._current_album.tracks):
            self._current_track_idx = next_idx
            self._play_current()
            return
        if self._repeat is RepeatMode.ALBUM:
            self._current_track_idx = 0
            self._play_current()
            return
        self._player.stop()
        self._current_track_idx = None
        self._refresh_play_marker()

    # ------------------------------------------------------------------
    # Player callbacks
    # ------------------------------------------------------------------

    def _on_track_end(self) -> None:
        self._end_pending = True

    def _on_track_failed(self, path: Path | str, message: str) -> None:
        log.warning("track failed: %s — %s", path, message)
        self._end_pending = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _truncate(value: str, max_len: int) -> str:
    """Cap `value` at `max_len` cells, ending with `…` when truncated."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"
