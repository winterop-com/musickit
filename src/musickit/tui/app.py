"""Textual app: ncmpcpp-styled library + now-playing + spectrum visualizer.

Composition:
  - `widgets.py` — every UI widget class (TopBar, SidebarStats, Visualizer,
    BrowserList, TrackList, StatusBar, KeyBar, ScanOverlay, …) + the
    central color palette + the `fmt_mmss` helper.
  - `commands.py` — the Ctrl+P palette provider that surfaces playback verbs.
  - `state.py` — `~/.config/musickit/state.toml` for persistent theme.
  - `app.py` (this file) — the orchestrator: wiring, actions, scan worker.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.timer import Timer
from textual.widgets import Input, ListItem, ListView, Static

from musickit import library as library_mod
from musickit import radio as radio_mod
from musickit.tui.advance import compute_next_track_idx
from musickit.tui.commands import MusickitCommands
from musickit.tui.filter import fold as _fold_for_match
from musickit.tui.formatters import format_station_row, format_track_row
from musickit.tui.player import AudioPlayer
from musickit.tui.state import load_state, save_state
from musickit.tui.types import (
    _BROWSER_DECORATION_PAD,
    _TREE_DEFAULT_WIDTH,
    _TREE_MAX_WIDTH,
    _TREE_MIN_WIDTH,
    _TREE_RESIZE_STEP,
    RepeatMode,
    _truncate,
)
from musickit.tui.widgets import (
    C_ACCENT,
    C_DIM,
    C_LABEL,
    C_PEAK,
    C_PLAYING,
    C_WARM,
    BrowserInfo,
    BrowserList,
    FilterInput,
    KeyBar,
    NowPlayingMeta,
    ProgressLine,
    ScanOverlay,
    SidebarStats,
    StatusBar,
    TopBar,
    TrackList,
    TrackTableHeader,
    Visualizer,
)

if TYPE_CHECKING:
    from textual.widget import Widget

    from musickit.library import LibraryAlbum, LibraryIndex, LibraryTrack
    from musickit.radio import RadioStation
    from musickit.tui.airplay import AirPlayController, AirPlayDevice
    from musickit.tui.subsonic_client import SubsonicClient

log = logging.getLogger(__name__)


class MusickitApp(App[None]):
    """ncmpcpp-styled three-row Textual app."""

    # Append our provider to Textual's defaults (system commands + bindings)
    # so the palette surfaces the playback verbs.
    COMMANDS = App.COMMANDS | {MusickitCommands}

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    /* Sidebar no longer needs the right-border separator now that each
       sidebar widget is itself a bordered panel — the borders provide
       enough visual separation on their own. */
    #sidebar { width: 34; padding: 0 1; }
    #main { width: 1fr; padding: 0 1 0 0; }
    #now-playing-row { height: auto; }
    /* The tracklist gets 2x the leftover-space share the visualizer
       does, so a tall window puts most of the new room into the track
       list (the primary thing on the page) rather than into ever-taller
       VU bars. min-height keeps a few rows visible even on short
       terminals. */
    #track-scroll { height: 2fr; min-height: 5; }
    /* Fullscreen: keep the visualizer + now-playing visible, hide everything
       library-related. The visualizer override MUST live in this app-level
       stylesheet (not in `Visualizer.DEFAULT_CSS`) — Textual doesn't
       reliably propagate `Screen.fullscreen Visualizer { ... }` declared
       inside the widget's own DEFAULT_CSS, so the base `max-height: 14`
       cap kept winning. Targeting `#visualizer` (the id assigned in
       `compose()`) avoids the issue entirely. `max-height: 200` is more
       cells than any reasonable terminal is tall — effectively
       unbounded. */
    Screen.fullscreen #sidebar { display: none; }
    Screen.fullscreen #track-header { display: none; }
    Screen.fullscreen #track-scroll { display: none; }
    Screen.fullscreen #status { display: none; }
    Screen.fullscreen #visualizer {
        height: 1fr;
        max-height: 200;
    }
    /* `v` toggle: hide the visualizer + its progress line so the
       tracklist gets all the leftover space. Useful for short albums
       where the meter dominates the screen. */
    Screen.no-viz #visualizer { display: none; }
    /* Scan-in-progress: hide the rest of the body so the centered scan
       overlay is the only thing visible. Avoids the awkward half-empty
       UI behind the scan card. */
    Screen.scanning #topbar { display: none; }
    Screen.scanning #body { display: none; }
    Screen.scanning #status { display: none; }
    Screen.scanning #keybar { display: none; }
    /* Command palette is a modal screen — keep it card-sized instead of
       sprawling across the full width. Targets Textual's internal layout. */
    CommandPalette {
        align: center top;
    }
    CommandPalette > Vertical {
        width: 80;
        max-width: 90%;
        margin-top: 4;
    }
    """

    # `show=True` here surfaces the binding in Textual's HelpPanel (the
    # `?` overlay). The bottom KeyBar widget still owns the always-on
    # quick-reference; HelpPanel is the comprehensive on-demand list.
    BINDINGS = [
        Binding("space", "toggle_pause", "Play / pause", show=True),
        Binding("enter", "play_selected", "Play selection", show=True),
        Binding("n", "next_track", "Next track", show=True),
        Binding("p", "prev_track", "Previous track", show=True),
        # mpv convention: `9` quieter, `0` louder. Both are digits so
        # they're unshifted on every layout (the `+/-` and `[/]` choices
        # we tried first all need shift / AltGr on Nordic Mac). The
        # original `+/-` keys still work for muscle-memory.
        Binding("0,plus,equals_sign", "vol_up", "Volume +", show=True),
        Binding("9,minus", "vol_down", "Volume -", show=True),
        # `←` / `→` are context-aware (see `action_left` / `action_right`):
        # they navigate between panes when one is focused, and only fall back
        # to seek when nothing's focused. Use `<` / `>` for always-on seek
        # (Shift+`,`/`.` — the "arrow" shifted variants of those keys).
        Binding("left", "left", "Navigate left", show=True),
        Binding("right", "right", "Navigate right", show=True),
        Binding("less_than_sign", "seek_back", "Seek backward", show=True),
        Binding("greater_than_sign", "seek_fwd", "Seek forward", show=True),
        Binding("s", "toggle_shuffle", "Shuffle", show=True),
        Binding("r", "cycle_repeat", "Repeat mode", show=True),
        Binding("f", "toggle_fullscreen", "Fullscreen viz", show=True),
        Binding("v", "toggle_visualizer", "Show / hide visualizer", show=True),
        Binding("g", "generate_playlist", "Generate mix from track", show=True),
        Binding("tab", "focus_next", "Focus next pane", show=True),
        Binding("ctrl+left", "tree_narrower", "Sidebar narrower", show=True),
        Binding("ctrl+right", "tree_wider", "Sidebar wider", show=True),
        Binding("backspace", "browser_up", "Browse up", show=True),
        Binding("ctrl+r,f5", "rescan_library", "Rescan library", show=True),
        Binding("ctrl+shift+r", "force_rescan_library", "Force rescan (wipe cache)", show=True),
        Binding("a", "airplay_picker", "AirPlay picker", show=True),
        Binding("slash", "start_filter", "Filter pane", show=True),
        Binding("e", "edit_tags", "Edit tags", show=True),
        Binding("question_mark", "toggle_help", "Toggle help", show=True),
        Binding("q,ctrl+c", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        root: Path | None,
        *,
        subsonic_client: SubsonicClient | None = None,
        airplay: AirPlayController | None = None,
        use_cache: bool = True,
        force_rescan: bool = False,
    ) -> None:
        super().__init__()
        self._root: Path | None = root
        self._subsonic_client: SubsonicClient | None = subsonic_client
        self._airplay: AirPlayController | None = airplay
        self._use_cache: bool = use_cache
        # Initial scan honours --full-rescan; subsequent Ctrl+R rescans do
        # a delta-validate via load_or_scan(force=False).
        self._pending_force_rescan: bool = force_rescan
        self._index: LibraryIndex | None = None
        self._player = AudioPlayer(airplay=airplay)
        self._player.on_track_end = self._on_track_end
        self._player.on_track_failed = self._on_track_failed
        self._player.on_metadata_change = self._on_stream_metadata_change
        self._current_album: LibraryAlbum | None = None
        self._current_track_idx: int | None = None
        self._marker_idx: int | None = None
        self._shuffle = False
        # Radio state.
        self._radio_stations: list[RadioStation] = []
        self._in_radio_view: bool = False
        # Mixes (saved auto-generated playlists) — drilled-into when the
        # user selects the "Mixes" entry in the browser. Mutually
        # exclusive with `_in_radio_view`.
        self._in_mixes_view: bool = False
        # Set by AudioPlayer.on_metadata_change (worker thread). Drained in
        # the UI tick so the `Now Playing` block updates with new ICY title.
        self._stream_metadata_dirty: bool = False
        self._repeat = RepeatMode.OFF
        self._end_pending = False
        # None = at top level (artists); a string = drilled into that artist.
        self._browse_artist: str | None = None
        # `/` filter — narrows the focused pane via case-insensitive
        # substring match. Empty = inactive.
        self._browser_filter: str = ""
        self._tracklist_filter: str = ""
        # Debounce timer for `on_resize` — reflowing track rows on every
        # resize-burst event holds the GIL long enough to click the audio.
        self._resize_reflow_timer: Timer | None = None
        # Snapshot of self.focused saved when entering fullscreen so we can
        # put it back on exit. Without this, hiding the focused widget via
        # CSS `display: none` drops focus, and Textual lands it on whatever
        # focusable widget remains (BrowserList) — surprising for users who
        # were last interacting with the TrackList.
        self._focus_before_fullscreen: Widget | None = None

    def on_resize(self, event: events.Resize) -> None:
        """Schedule a debounced row reflow after the user stops resizing.

        The TrackTableHeader and visualizer recompute their layout from
        `self.size.width` every render, so they adapt automatically. Each
        track row, on the other hand, is a `ListItem(Static(...))` with a
        label baked in at populate time — without re-rendering, long
        titles stay clipped at the OLD title width.

        Resize events fire continuously during a drag (potentially per
        cell) — running the per-row reflow on each fire holds the GIL
        long enough to starve the audio callback and produce mid-track
        clicks (xruns). Debouncing collapses the burst into one update once
        the user stops resizing.
        """
        del event
        # Cancel any pending reflow; schedule a new one.
        if self._resize_reflow_timer is not None:
            self._resize_reflow_timer.stop()
        self._resize_reflow_timer = self.set_timer(0.15, self._reflow_track_rows)

    def _reflow_track_rows(self) -> None:
        """Re-render every track row's label with the current title_width."""
        from musickit.tui.formatters import compute_title_width

        # Clear the debounce flag so `_refresh_visualizer` resumes ticking.
        self._resize_reflow_timer = None
        try:
            tracklist = self.query_one(TrackList)
        except NoMatches:
            return
        title_width = compute_title_width(tracklist.size.width, header_padding=2)
        if self._in_radio_view:
            for child in tracklist.children:
                if not isinstance(child, ListItem):
                    continue
                station = getattr(child, "station", None)
                idx = getattr(child, "track_index", None)
                if station is None or not isinstance(idx, int):
                    continue
                rows = child.query(Static)
                if rows:
                    rows.first().update(
                        format_station_row(idx, station, marker=(idx == self._marker_idx), title_width=title_width)
                    )
            return
        if self._current_album is None:
            return
        for child in tracklist.children:
            if not isinstance(child, ListItem):
                continue
            idx = getattr(child, "track_index", None)
            if not isinstance(idx, int):
                continue
            if not (0 <= idx < len(self._current_album.tracks)):
                continue
            track = self._current_album.tracks[idx]
            rows = child.query(Static)
            if rows:
                rows.first().update(
                    format_track_row(
                        idx,
                        track,
                        self._current_album,
                        marker=(idx == self._current_track_idx),
                        title_width=title_width,
                    )
                )

    def on_track_list_focus_lost(self, event: TrackList.FocusLost) -> None:
        """When focus leaves TrackList, snap its cursor to the playing track.

        Otherwise the highlight on a non-playing row sticks around while
        the user navigates the browser, which looks like a stale
        selection. Coming back to the tracklist should land on what's
        playing, not on the row the user happened to last hover.
        """
        del event  # unused
        self._snap_tracklist_cursor_to_playing_track()

    def watch_theme(self, theme: str) -> None:
        """Persist theme changes (e.g. via the command palette) to disk."""
        state = load_state()
        if state.get("theme") == theme:
            return
        state["theme"] = theme
        save_state(state)

    async def on_unmount(self) -> None:
        """Tear down the audio subprocess + Subsonic httpx pool + AirPlay loop on app exit.

        Without this the process hangs after `q`: the audio subprocess is
        a daemon, but a clean shutdown via `_player.shutdown()` joins the
        engine, drains its queues, and avoids the BrokenPipeError that
        would otherwise show up at interpreter shutdown. httpx leaves
        connection-pool sockets open without close(); pyatv's asyncio
        thread keeps the interpreter alive without disconnect().
        """
        try:
            self._player.stop()
        except Exception:  # pragma: no cover — best effort on shutdown
            pass
        try:
            self._player.shutdown()
        except Exception:  # pragma: no cover
            pass
        if self._subsonic_client is not None:
            self._subsonic_client.close()
        if self._airplay is not None:
            try:
                self._airplay.disconnect()
            except Exception:  # pragma: no cover
                pass

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield SidebarStats(id="stats")
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
        # Restore the user's theme choice (set via the command palette) before
        # the first paint, so we don't flash the default theme on startup.
        state = load_state()
        saved_theme = state.get("theme")
        if isinstance(saved_theme, str):
            try:
                self.theme = saved_theme
            except Exception:  # pragma: no cover — bad/old theme name
                pass
        # ReplayGain mode persists in state.toml (default 'auto'). Validated
        # against the small set of supported modes; anything else falls back
        # to 'auto' silently.
        saved_rg = state.get("replaygain")
        if isinstance(saved_rg, str) and saved_rg in ("auto", "track", "album", "off"):
            self._player.set_replaygain_mode(saved_rg)
        # Visualizer ticks at 30 FPS; status text only at 4 FPS to save
        # redraws on text that doesn't need to change frame-to-frame.
        self.set_interval(1 / 30, self._refresh_visualizer)
        self.set_interval(0.25, self._refresh_status)
        self.set_interval(0.05, self._drain_end_pending)
        self.set_interval(0.5, self._drain_stream_metadata)
        # Seed the user's `radio.toml` template on first run, then load.
        try:
            radio_mod.seed_default_config()
        except OSError:  # pragma: no cover — read-only home, etc.
            pass
        self._radio_stations = radio_mod.load_stations()
        if self._subsonic_client is not None:
            self._show_scan_overlay("[bold cyan]Loading library from server…[/]")
            self._scan_library_async(initial=True)
            return
        if self._root is None:
            # Radio-only launch: skip the scan, drop directly into stations.
            self._populate_sidebar_stats()
            self._populate_browser()
            self._open_radio_view()
            return
        self._show_scan_overlay("[bold cyan]Scanning library…[/]")
        self._scan_library_async(initial=True)

    # ------------------------------------------------------------------
    # Sidebar / browser population
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
        # Invalidate the cursor BEFORE mutating children. Otherwise a stale
        # index from the prior list (e.g. row 32 of a long album list) can
        # leak into the new list and crash the next ↑/↓ keypress.
        browser.index = None
        browser.clear()
        if self._index is None or not self._index.albums:
            browser.border_title = "Browse"
            radio_item = ListItem(Static(f" [{C_ACCENT}]📻[/] [bold]Radio[/]  [dim]({len(self._radio_stations)})[/]"))
            radio_item.entry_kind = "radio"  # type: ignore[attr-defined]
            radio_item.entry_data = None  # type: ignore[attr-defined]
            browser.append(radio_item)
            if self._root is not None:
                browser.append(ListItem(Static("[dim](no albums)[/]")))
            self._fit_sidebar_width()
            self.call_after_refresh(self._set_browser_cursor, 0)
            return
        if self._browse_artist is None:
            browser.border_title = "Browse"
            self._populate_browser_artists(browser)
        else:
            browser.border_title = f"Browse · {self._browse_artist}"
            self._populate_browser_albums(browser, self._browse_artist)
        self._fit_sidebar_width()
        target_index = 0
        if self._browse_artist is not None and len(browser.children) > 1:
            target_index = 1
        self.call_after_refresh(self._set_browser_cursor, target_index)

    def _set_browser_cursor(self, target_index: int) -> None:
        """Place the cursor on `target_index` after children mount.

        Also refocuses the browser (Textual sometimes drops focus when a
        widget's children get mass-replaced), scrolls the highlighted row
        into view (the implicit scroll-on-index doesn't always fire post
        clear+append), and refreshes the info panel — `index =` doesn't
        always fire `Highlighted` post clear+append.
        """
        browser = self.query_one(BrowserList)
        if target_index >= len(browser.children):
            return
        browser.index = target_index
        # Don't yank focus back to the browser if the user is typing in a
        # filter input — the filter is a sibling and would lose focus on
        # every keystroke otherwise.
        if not browser.has_focus and not isinstance(self.focused, FilterInput):
            browser.focus()
        item = browser.children[target_index]
        self._update_browser_info(item if isinstance(item, ListItem) else None)

        # Force the new cursor row into view. The layout regions of
        # freshly-appended children aren't always measured at the first
        # `call_after_refresh` (which is how we got here), so a single
        # `scroll_to_widget` can land on the old viewport. Chain ANOTHER
        # `call_after_refresh` so the layout pass that placed the
        # children has settled, then scroll the cursor row into view.
        def _scroll_into_view() -> None:
            try:
                row = browser.children[target_index]
            except IndexError:
                return
            if isinstance(row, ListItem):
                row.scroll_visible(animate=False, top=False)

        self.call_after_refresh(_scroll_into_view)

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
        if self._index is None:
            return
        if self._browse_artist is None:
            longest = max((len(a.artist_dir) for a in self._index.albums), default=0)
        else:
            longest = max(
                (len(a.album_dir) for a in self._index.albums if a.artist_dir == self._browse_artist),
                default=0,
            )
        target = max(_TREE_DEFAULT_WIDTH, longest + _BROWSER_DECORATION_PAD)
        target = min(_TREE_MAX_WIDTH, max(_TREE_MIN_WIDTH, target))
        sidebar = self.query_one("#sidebar")
        sidebar.styles.width = target

    def _populate_browser_artists(self, browser: BrowserList) -> None:
        assert self._index is not None
        # Hide Radio + Mixes while a filter is active — the user is searching
        # for an artist, surfacing those entries is just noise.
        if not self._browser_filter:
            radio_item = ListItem(Static(f" [{C_ACCENT}]📻[/] [bold]Radio[/]  [dim]({len(self._radio_stations)})[/]"))
            radio_item.entry_kind = "radio"  # type: ignore[attr-defined]
            radio_item.entry_data = None  # type: ignore[attr-defined]
            browser.append(radio_item)
            n_mixes = self._count_mixes()
            mixes_item = ListItem(Static(f" [{C_ACCENT}]♫[/] [bold]Mixes[/]  [dim]({n_mixes})[/]"))
            mixes_item.entry_kind = "mixes"  # type: ignore[attr-defined]
            mixes_item.entry_data = None  # type: ignore[attr-defined]
            browser.append(mixes_item)
        by_artist: dict[str, list[LibraryAlbum]] = {}
        for album in self._index.albums:
            by_artist.setdefault(album.artist_dir, []).append(album)
        max_name = _TREE_MAX_WIDTH - _BROWSER_DECORATION_PAD
        needle = _fold_for_match(self._browser_filter)
        matched = 0
        for artist in sorted(by_artist, key=str.lower):
            if needle and needle not in _fold_for_match(artist):
                continue
            count = len(by_artist[artist])
            name = _truncate(artist, max_name)
            label = f" [{C_ACCENT}]▸[/] {name}  [dim]({count})[/]"
            item = ListItem(Static(label))
            item.entry_kind = "artist"  # type: ignore[attr-defined]
            item.entry_data = artist  # type: ignore[attr-defined]
            browser.append(item)
            matched += 1
        if needle and matched == 0:
            browser.append(ListItem(Static("[dim](no matches)[/]")))

    def _populate_browser_albums(self, browser: BrowserList, artist: str) -> None:
        assert self._index is not None
        # `..` Back row stays visible regardless of filter — always need an exit.
        up_item = ListItem(Static(f" [{C_ACCENT}]..[/]  [dim]Back[/]"))
        up_item.entry_kind = "up"  # type: ignore[attr-defined]
        up_item.entry_data = None  # type: ignore[attr-defined]
        browser.append(up_item)
        artist_albums = sorted(
            (a for a in self._index.albums if a.artist_dir == artist),
            key=lambda a: a.album_dir.lower(),
        )
        max_name = _TREE_MAX_WIDTH - _BROWSER_DECORATION_PAD
        needle = _fold_for_match(self._browser_filter)
        matched = 0
        for album in artist_albums:
            if needle and needle not in _fold_for_match(album.album_dir):
                continue
            warn = f" [{C_PEAK}]⚠[/]" if album.warnings else ""
            name = _truncate(album.album_dir, max_name)
            label = f" [{C_ACCENT}]♪[/] {name}{warn}"
            item = ListItem(Static(label))
            item.entry_kind = "album"  # type: ignore[attr-defined]
            item.entry_data = album  # type: ignore[attr-defined]
            browser.append(item)
            matched += 1
        if needle and matched == 0:
            browser.append(ListItem(Static("[dim](no matches)[/]")))

    # ------------------------------------------------------------------
    # ListView event dispatch
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        browser = self.query_one(BrowserList)
        tracklist = self.query_one(TrackList)
        if event.list_view is browser:
            self._handle_browser_selection(event.item)
        elif event.list_view is tracklist:
            station = getattr(event.item, "station", None)
            if isinstance(station, radio_mod.RadioStation):
                self._play_station(station)
                return
            mix_path = getattr(event.item, "mix_path", None)
            if isinstance(mix_path, Path):
                self._play_mix(mix_path)
                return
            idx = getattr(event.item, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()

    def on_progress_line_seek(self, event: ProgressLine.Seek) -> None:
        """Click anywhere on the progress bar → seek to that position."""
        if self._player.duration <= 0:
            return
        self._player.seek(event.seconds)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        browser = self.query_one(BrowserList)
        if event.list_view is not browser:
            return
        self._update_browser_info(event.item)
        self._preview_highlighted_album(event.item)

    def _preview_highlighted_album(self, item: ListItem | None) -> None:
        """Sync the tracklist to the album the browser cursor is hovering on.

        Without this, the tracklist persists with whatever was last opened,
        which feels stale when the browser cursor has moved on. We update
        on every highlight so what's shown on the right always matches
        what's selected on the left. Skipped for non-album rows (artists,
        the `..` back row) and for Subsonic shells whose tracks aren't
        hydrated yet — clicking those still uses the Enter path which
        kicks off the lazy-load.
        """
        if item is None:
            return
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind != "album" or not isinstance(data, library_mod.LibraryAlbum):
            return
        if self._current_album is data:
            return  # already showing this album
        # Don't fire a network request from a hover — Enter still does that.
        if not data.tracks and self._subsonic_client is not None:
            return
        self._clear_tracklist_filter()
        self._set_current_album(data, track_idx=None)
        self._repopulate_playlist()

    def _handle_browser_selection(self, item: ListItem) -> None:
        kind_first = getattr(item, "entry_kind", None)
        if kind_first == "radio":
            self._open_radio_view()
            self.query_one(TrackList).focus()
            return
        if kind_first == "mixes":
            self._open_mixes_view()
            self.query_one(TrackList).focus()
            return
        return self._handle_browser_selection_default(item)

    def _open_radio_view(self) -> None:
        """Populate the right-pane track list with the curated radio stations."""
        self._clear_tracklist_filter()
        self._in_radio_view = True
        self._in_mixes_view = False
        self._current_album = None
        self._current_track_idx = None
        self._marker_idx = None
        self._repopulate_radio_playlist()

    def _open_mixes_view(self) -> None:
        """Populate the right-pane track list with the saved .m3u8 playlists.

        Each row carries the source path on `entry_data`; selection
        resolves the playlist into a virtual `LibraryAlbum` (same shape
        as `g`) and starts playback.
        """
        self._clear_tracklist_filter()
        self._in_mixes_view = True
        self._in_radio_view = False
        self._current_album = None
        self._current_track_idx = None
        self._marker_idx = None
        self._repopulate_mixes_playlist()

    def _count_mixes(self) -> int:
        """Number of saved .m3u8 files; cheap glob, called from browser paint."""
        if self._root is None:
            return 0
        pdir = self._root / library_mod.INDEX_DIR_NAME / "playlists"
        if not pdir.is_dir():
            return 0
        return len(list(pdir.glob("*.m3u8")))

    def _list_mix_files(self) -> list[Path]:
        """Sorted list of saved playlist files."""
        if self._root is None:
            return []
        pdir = self._root / library_mod.INDEX_DIR_NAME / "playlists"
        if not pdir.is_dir():
            return []
        return sorted(pdir.glob("*.m3u8"))

    def _repopulate_mixes_playlist(self) -> None:
        """Render saved mixes as TrackList rows; each carries `entry_data=Path`."""
        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        files = self._list_mix_files()
        if not files:
            tracklist.append(
                ListItem(Static("[dim]No saved mixes yet — press [bold]g[/] on a track to create one.[/]"))
            )
            return
        for i, f in enumerate(files):
            try:
                track_count = sum(
                    1 for line in f.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")
                )
            except OSError:
                track_count = 0
            label = f" [{C_ACCENT}]♫[/]  {f.stem}  [dim]({track_count} tracks)[/]"
            item = ListItem(Static(label, id=f"mix-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            item.mix_path = f  # type: ignore[attr-defined]
            tracklist.append(item)
        self.call_after_refresh(self._set_tracklist_cursor, 0)

    def _load_mix(self, m3u8_path: Path) -> LibraryAlbum | None:
        """Read a saved .m3u8 and resolve its tracks against the live index.

        Tracks whose paths no longer exist (file moved / deleted /
        renamed since the mix was generated) are silently skipped so a
        stale mix degrades gracefully instead of crashing playback.
        Returns None if no track survives the resolution.
        """
        if self._index is None:
            return None
        from musickit.library import LibraryAlbum as _LibraryAlbum
        from musickit.library import LibraryTrack as _LibraryTrack
        from musickit.playlist.io import read_m3u8

        try:
            paths = read_m3u8(m3u8_path)
        except OSError:
            return None
        # Build a path → LibraryTrack lookup once. resolve() to normalise
        # any `..`-using relative entries from the .m3u8.
        by_path: dict[Path, _LibraryTrack] = {}
        for album in self._index.albums:
            for t in album.tracks:
                by_path[t.path.resolve()] = t
        resolved: list[_LibraryTrack] = []
        for p in paths:
            try:
                rp = p.resolve()
            except OSError:
                continue
            if rp in by_path:
                resolved.append(by_path[rp])
        if not resolved:
            return None
        return _LibraryAlbum(
            path=m3u8_path.parent,
            artist_dir="Mix",
            album_dir=m3u8_path.stem,
            tag_album=m3u8_path.stem,
            tag_album_artist="Mix",
            track_count=len(resolved),
            tracks=resolved,
        )

    def _handle_browser_selection_default(self, item: ListItem) -> None:
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind == "up":
            self._pop_browser_one_level()
        elif kind == "artist" and isinstance(data, str):
            self._clear_browser_filter()
            self._browse_artist = data
            self._populate_browser()
        elif kind == "album" and isinstance(data, library_mod.LibraryAlbum):
            self._clear_tracklist_filter()
            self._in_radio_view = False
            # Re-entering the SAME album that's currently playing keeps
            # `_current_track_idx` so the ▶ marker, NowPlayingMeta, and the
            # cursor land on the playing track. Switching to a DIFFERENT
            # album resets to None (default to row 0).
            same_album = self._current_album is data and self._current_track_idx is not None
            preserved_idx = self._current_track_idx if same_album else None
            self._set_current_album(data, track_idx=preserved_idx)
            # Subsonic lazy-load: shell albums have no tracks until clicked.
            # Show a "Loading…" placeholder + kick a worker; the worker calls
            # back to repopulate when the API returns.
            if not data.tracks and self._subsonic_client is not None and data.subsonic_id is not None:
                self._show_loading_tracklist()
                self.query_one(TrackList).focus()
                self._hydrate_album_async(data)
                return
            self._repopulate_playlist()
            self.query_one(TrackList).focus()

    def _show_loading_tracklist(self) -> None:
        """Drop a single Loading… row into the tracklist while the API call is in flight."""
        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        tracklist.append(ListItem(Static(f"[{C_DIM}]Loading tracks…[/]")))

    @work(thread=True, exclusive=True, group="hydrate")
    def _hydrate_album_async(self, album: LibraryAlbum) -> None:
        """Fetch tracks for `album` via getAlbum, then repopulate on the UI thread."""
        if self._subsonic_client is None:
            return
        from musickit.tui.subsonic_client import SubsonicError, hydrate_album_tracks

        try:
            hydrate_album_tracks(self._subsonic_client, album)
        except SubsonicError as exc:
            log.warning("hydrate failed for %s: %s", album.album_dir, exc)
            self.call_from_thread(self._on_hydrate_failed, album, str(exc))
            return
        self.call_from_thread(self._on_hydrate_complete, album)

    def _on_hydrate_complete(self, album: LibraryAlbum) -> None:
        # Guard against the user having moved on to a different album while
        # the API call was in flight — only repaint if we're still on this one.
        if self._current_album is album:
            self._repopulate_playlist()

    def _on_hydrate_failed(self, album: LibraryAlbum, message: str) -> None:
        if self._current_album is not album:
            return
        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        tracklist.append(ListItem(Static(f"[red]Failed to load tracks: {message}[/]")))

    # ------------------------------------------------------------------
    # Playlist rendering (library tracks + radio stations)
    # ------------------------------------------------------------------

    def _set_current_album(self, album: LibraryAlbum, *, track_idx: int | None) -> None:
        self._current_album = album
        # If track_idx wasn't supplied, derive it from the player's current
        # source so the ▶ marker keeps showing on the playing row when the
        # user re-enters the playing album after browsing somewhere else.
        if track_idx is None:
            track_idx = self._playing_track_idx_in(album)
        self._current_track_idx = track_idx

    def _playing_track_idx_in(self, album: LibraryAlbum) -> int | None:
        """Return the playing track's index within `album`, or None.

        Drives the ▶ marker without a separate `_playing_album` field —
        we already have the source-of-truth in the AudioPlayer.
        """
        src = self._player.current_source
        if src is None:
            return None
        for i, track in enumerate(album.tracks):
            # Local files: compare Path; subsonic streams: compare stream_url.
            if track.path == src or (track.stream_url and track.stream_url == src):
                return i
        return None

    def _repopulate_playlist(self) -> None:
        from musickit.tui.formatters import compute_title_width

        tracklist = self.query_one(TrackList)
        # Same defer-cursor pattern as the browser to avoid the
        # second-album-no-highlight bug.
        tracklist.index = None
        tracklist.clear()
        if self._current_album is None:
            self._marker_idx = None
            return
        album = self._current_album
        # Resolve the marker from the player's current source so the ▶
        # appears whenever the user is viewing the playing album, even
        # after browsing somewhere else and back.
        playing_idx = self._playing_track_idx_in(album)
        self._current_track_idx = playing_idx
        needle = _fold_for_match(self._tracklist_filter)
        title_width = compute_title_width(tracklist.size.width, header_padding=2)
        matched = 0
        for i, track in enumerate(album.tracks):
            if needle:
                hay = _fold_for_match(f"{track.title or ''} {track.artist or ''}")
                if needle not in hay:
                    continue
            label = format_track_row(i, track, album, marker=(i == playing_idx), title_width=title_width)
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            tracklist.append(item)
            matched += 1
        self._marker_idx = self._current_track_idx
        if needle and matched == 0:
            tracklist.append(ListItem(Static("[dim](no matches)[/]")))
            return
        # Defer to dodge the second-album-no-highlight bug (children
        # not yet mounted at this point — `append()` returns an
        # AwaitMount). The deferred call resolves the visible row at
        # execution time so it sees the current filter state.
        self.call_after_refresh(self._set_tracklist_cursor_for_track, self._current_track_idx)

    def _visible_row_for_track(self, tracklist: TrackList, track_idx: int | None) -> int | None:
        """Map a model track index to its visible row index (or first row when not visible)."""
        if track_idx is not None:
            for visible_idx, child in enumerate(tracklist.children):
                if getattr(child, "track_index", None) == track_idx:
                    return visible_idx
        # Current track not visible (filtered out) — fall back to the first
        # row that has a track_index, if any.
        for visible_idx, child in enumerate(tracklist.children):
            if getattr(child, "track_index", None) is not None:
                return visible_idx
        return None

    def _set_tracklist_cursor_for_track(self, model_track_idx: int | None) -> None:
        """Place the cursor on the visible row for `model_track_idx` (or first row)."""
        tracklist = self.query_one(TrackList)
        target = self._visible_row_for_track(tracklist, model_track_idx)
        if target is None:
            return
        if 0 <= target < len(tracklist.children):
            tracklist.index = target

    def _set_tracklist_cursor(self, target: int) -> None:
        tracklist = self.query_one(TrackList)
        if 0 <= target < len(tracklist.children):
            tracklist.index = target

    def _repopulate_radio_playlist(self) -> None:
        from musickit.tui.formatters import compute_title_width

        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        if not self._radio_stations:
            tracklist.append(ListItem(Static("[dim]No stations configured. Edit `~/.config/musickit/radio.toml`.[/]")))
            return
        needle = _fold_for_match(self._tracklist_filter)
        title_width = compute_title_width(tracklist.size.width, header_padding=2)
        matched = 0
        for i, station in enumerate(self._radio_stations):
            if needle and needle not in _fold_for_match(station.name):
                continue
            label = format_station_row(i, station, marker=False, title_width=title_width)
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            item.station = station  # type: ignore[attr-defined]
            tracklist.append(item)
            matched += 1
        if needle and matched == 0:
            tracklist.append(ListItem(Static("[dim](no matches)[/]")))
        self.call_after_refresh(self._set_tracklist_cursor, 0)

    def _play_mix(self, m3u8_path: Path) -> None:
        """Resolve a saved .m3u8 to a virtual album and start playing it."""
        virtual = self._load_mix(m3u8_path)
        if virtual is None:
            self.notify(
                f"Couldn't load mix from {m3u8_path.name}: no resolvable tracks left.",
                severity="warning",
            )
            return
        self._in_mixes_view = False
        self._set_current_album(virtual, track_idx=0)
        self._repopulate_playlist()
        self._play_current()

    def _play_station(self, station: RadioStation) -> None:
        from musickit.tui.formatters import compute_title_width

        idx = self._radio_stations.index(station) if station in self._radio_stations else None
        prev = self._marker_idx
        self._current_track_idx = idx
        self._marker_idx = idx
        self._player.play(station.url)
        title_width = compute_title_width(self.query_one(TrackList).size.width, header_padding=2)
        if prev is not None and prev != idx:
            try:
                self.query_one(f"#track-row-{prev}", Static).update(
                    format_station_row(prev, self._radio_stations[prev], marker=False, title_width=title_width)
                )
            except Exception:  # pragma: no cover
                pass
        if idx is not None:
            try:
                self.query_one(f"#track-row-{idx}", Static).update(
                    format_station_row(idx, station, marker=True, title_width=title_width)
                )
            except Exception:  # pragma: no cover
                pass

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
        from musickit.tui.formatters import compute_title_width

        if self._current_album is None:
            return
        try:
            label_widget = self.query_one(f"#track-row-{idx}", Static)
        except Exception:
            return
        track = self._current_album.tracks[idx]
        title_width = compute_title_width(self.query_one(TrackList).size.width, header_padding=2)
        label_widget.update(format_track_row(idx, track, self._current_album, marker=marker, title_width=title_width))

    # ------------------------------------------------------------------
    # Status refresh (UI ticks)
    # ------------------------------------------------------------------

    def _refresh_visualizer(self) -> None:
        """High-FPS visualizer tick — runs the FFT off the audio thread.

        Skipped while a resize burst is in flight: the visualizer fires
        30×/sec and adds GIL pressure on top of Textual's layout reflow.
        Letting the bars freeze for a few hundred milliseconds during a
        drag is invisible; preventing audio clicks is not.

        When paused, the engine stops publishing new band data, so the
        shared-memory levels stay frozen at whatever the last audio
        chunk rendered. Apply a per-frame decay locally so the bars
        gracefully fade to zero over ~1s instead of looking dead. Same
        treatment when stopped (no track loaded). Resumes pass-through
        the moment playback starts again.
        """
        if self._resize_reflow_timer is not None:
            return
        try:
            visualizer = self.query_one(Visualizer)
        except NoMatches:
            # Tick fired after the DOM was torn down (e.g. mid-shutdown
            # in tests). Bail; the timer will be reaped along with the app.
            return
        if self._player.is_playing:
            self._player.update_band_levels()
            visualizer.levels = self._player.band_levels
        else:
            # Paused / stopped: decay the previously-shown levels toward 0.
            # 0.858 ≈ 0.01 ** (1/30) → bars reach ~1% in ~1s at 30 FPS,
            # matching the visualizer's natural release feel.
            current = list(visualizer.levels)
            decayed = [v * 0.858 if v > 0.005 else 0.0 for v in current]
            # Avoid the cost of a no-op reactive write once everything's
            # already at the floor — the visualizer redraw isn't free.
            if any(v > 0.0 for v in decayed) or any(v > 0.0 for v in current):
                visualizer.levels = decayed

    def _refresh_status(self) -> None:
        try:
            meta = self.query_one(NowPlayingMeta)
            progress = self.query_one(ProgressLine)
            status = self.query_one(StatusBar)
        except NoMatches:
            return
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
                meta.genre = "—"
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
        if not self._end_pending:
            return
        self._end_pending = False
        try:
            self._advance_track()
        except NoMatches:
            # Timer fired during/after unmount — widget queries inside
            # the advance chain will fail; safe to skip this tick.
            return

    def _drain_stream_metadata(self) -> None:
        if not self._stream_metadata_dirty:
            return
        self._stream_metadata_dirty = False
        self._refresh_status()

    def _on_stream_metadata_change(self) -> None:
        self._stream_metadata_dirty = True

    def _currently_playing_track(self) -> LibraryTrack | None:
        if self._current_album is None or self._current_track_idx is None:
            return None
        if 0 <= self._current_track_idx < len(self._current_album.tracks):
            return self._current_album.tracks[self._current_track_idx]
        return None

    # ------------------------------------------------------------------
    # Actions / keybindings
    # ------------------------------------------------------------------

    def action_play_selected(self) -> None:
        tracklist = self.query_one(TrackList)
        browser = self.query_one(BrowserList)
        focused = self.focused
        if focused is tracklist and tracklist.highlighted_child is not None:
            # Radio rows carry both `station` and `track_index`. The station
            # check has to come first — falling through to `_play_current`
            # in radio view would no-op (no `_current_album`) and the user's
            # Enter press would be silently swallowed. Mixes follow the
            # same routing rule (mix_path takes precedence over track_index).
            station = getattr(tracklist.highlighted_child, "station", None)
            if isinstance(station, radio_mod.RadioStation):
                self._play_station(station)
                return
            mix_path = getattr(tracklist.highlighted_child, "mix_path", None)
            if isinstance(mix_path, Path):
                self._play_mix(mix_path)
                return
            idx = getattr(tracklist.highlighted_child, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()
                return
        if focused is browser and browser.highlighted_child is not None:
            self._handle_browser_selection(browser.highlighted_child)
            return
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
        """←: navigation only — never seek (use `<`)."""
        focused = self.focused
        if isinstance(focused, BrowserList):
            if self._browse_artist is not None:
                self._pop_browser_one_level()
            return
        if isinstance(focused, TrackList):
            self.query_one(BrowserList).focus()

    def action_right(self) -> None:
        """→: drill into the browser only — never seek (use `>`)."""
        focused = self.focused
        if isinstance(focused, BrowserList) and focused.highlighted_child is not None:
            self._handle_browser_selection(focused.highlighted_child)

    def _pop_browser_one_level(self) -> None:
        prior_artist = self._browse_artist
        self._clear_browser_filter()
        self._browse_artist = None
        self._populate_browser()
        # When leaving the album view, reset the tracklist cursor back to
        # the playing track (or clear it if nothing's playing) so the row
        # the user happened to be hovering on doesn't keep its highlight
        # while the user is browsing albums.
        self._snap_tracklist_cursor_to_playing_track()
        if prior_artist is None or self._index is None:
            return
        # Compute prior-artist row index from the data model — `browser.children`
        # can still report stale items mid clear+append. Rows 0 and 1 are the
        # Radio + Mixes entries that `_populate_browser_artists` always
        # prepends (when no filter is active), so the artist's row is
        # `data_index + 2`.
        artist_names = sorted({a.artist_dir for a in self._index.albums}, key=str.lower)
        try:
            prior_idx = artist_names.index(prior_artist) + 2
        except ValueError:
            return
        self.call_after_refresh(self._set_browser_cursor, prior_idx)

    def _snap_tracklist_cursor_to_playing_track(self) -> None:
        """Move the tracklist cursor to the playing track (or clear it).

        Fires on every TrackList blur. Rapid pane-toggling (Tab between
        BrowserList and TrackList) used to schedule a redundant refresh
        every time even when the cursor was already at the playing
        track — each scheduled refresh briefly holds the GIL on render,
        which can starve the audio callback. Short-circuit when the
        cursor is already where we want it.
        """
        try:
            tracklist = self.query_one(TrackList)
        except NoMatches:
            return
        if self._current_track_idx is not None:
            target = self._visible_row_for_track(tracklist, self._current_track_idx)
            if target is not None and tracklist.index == target:
                return
            self.call_after_refresh(self._set_tracklist_cursor_for_track, self._current_track_idx)
        else:
            from typing import cast as _cast

            tracklist.index = _cast("int | None", None)

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

    def _resize_sidebar(self, delta: int) -> None:
        sidebar = self.query_one("#sidebar")
        current = sidebar.styles.width
        try:
            current_cells = int(current.value) if current is not None else _TREE_DEFAULT_WIDTH
        except (TypeError, ValueError):
            current_cells = _TREE_DEFAULT_WIDTH
        new_width = max(_TREE_MIN_WIDTH, min(_TREE_MAX_WIDTH, current_cells + delta))
        sidebar.styles.width = new_width

    def action_browser_up(self) -> None:
        if self._browse_artist is not None:
            self._pop_browser_one_level()

    def action_rescan_library(self) -> None:
        if self._root is None:
            self._radio_stations = radio_mod.load_stations()
            self._populate_browser()
            if self._in_radio_view:
                self._repopulate_radio_playlist()
            return
        self._show_scan_overlay("[bold cyan]Rescanning library…[/]")
        self._scan_library_async(initial=False)

    def action_force_rescan_library(self) -> None:
        """`Ctrl+Shift+R` — wipe the SQLite cache and rebuild from the filesystem.

        Use when a normal rescan (`Ctrl+R`) doesn't pick up changes —
        e.g. files mutated outside the watcher's view, or the cache
        looks corrupted. Equivalent to `musickit library index rebuild`
        plus relaunching the TUI.
        """
        if self._root is None:
            return
        self._pending_force_rescan = True
        self._show_scan_overlay("[bold cyan]Force-rescanning library…[/]")
        self._scan_library_async(initial=False)

    def action_toggle_visualizer(self) -> None:
        """`v` — hide / show the visualizer panel.

        On albums where the meter dominates the screen (a 2-track album
        on a typical-height window) the user often just wants the
        tracklist. Toggling adds / removes the `no-viz` class on the
        screen; CSS does the rest.
        """
        if self.screen.has_class("no-viz"):
            self.screen.remove_class("no-viz")
        else:
            self.screen.add_class("no-viz")

    def action_generate_playlist(self) -> None:
        """`g` — seed an auto-generated mix from the highlighted track.

        Builds a 60-min playlist anchored to the highlighted (or
        currently-playing) track, writes it to
        `<root>/.musickit/playlists/<slug>.m3u8` for cross-tool reuse,
        wraps the result in a virtual `LibraryAlbum` so the existing
        TrackList / playback flow can handle it, and starts playing.
        """
        if self._index is None:
            self.notify("Library not loaded yet.", severity="warning")
            return
        if self._subsonic_client is not None:
            self.notify(
                "Mix generation isn't available in Subsonic-client mode.",
                severity="warning",
            )
            return
        seed = self._resolve_playlist_seed()
        if seed is None:
            self.notify(
                "Highlight a track in the tracklist first.",
                severity="warning",
            )
            return

        from musickit import playlist as playlist_mod
        from musickit.cli.playlist import _playlists_dir, _slug

        try:
            result = playlist_mod.generate(self._index, seed, target_minutes=60.0)
        except ValueError as exc:
            self.notify(f"Couldn't generate: {exc}", severity="error")
            return

        # Persist to disk so other tools (VLC, Subsonic clients) can use
        # the same mix. Failure is non-fatal — playback still works.
        if self._root is not None:
            try:
                out = _playlists_dir(self._root) / f"{_slug(result.name)}.m3u8"
                playlist_mod.write_m3u8(result, out)
            except OSError:
                pass

        # Wrap the result in a virtual album so TrackList / next/prev /
        # repeat / shuffle all just work. The path is set to the
        # playlists dir for any future "open the on-disk file" hooks.
        from musickit.library import LibraryAlbum  # local import; module-level would cycle on type stubs

        virtual_path = (
            self._root / library_mod.INDEX_DIR_NAME / "playlists"
            if self._root is not None
            else Path("/tmp/musickit-mix")
        )
        virtual = LibraryAlbum(
            path=virtual_path,
            artist_dir="Mix",
            album_dir=result.name,
            tag_album=result.name,
            tag_album_artist="Mix",
            track_count=len(result.tracks),
            tracks=list(result.tracks),
        )
        self._set_current_album(virtual, track_idx=0)
        self._repopulate_playlist()
        self._play_current()
        self.notify(
            f"Generated [bold]{result.name}[/]: {len(result.tracks)} tracks, {result.actual_seconds / 60:.0f} min",
        )

    def _resolve_playlist_seed(self) -> LibraryTrack | None:
        """Return the highlighted-or-playing track for `g`."""
        if self._current_album is None:
            return None
        tracklist = self.query_one(TrackList)
        if tracklist.highlighted_child is not None:
            idx = getattr(tracklist.highlighted_child, "track_index", None)
            if isinstance(idx, int) and 0 <= idx < len(self._current_album.tracks):
                return self._current_album.tracks[idx]
        if self._current_track_idx is not None and 0 <= self._current_track_idx < len(self._current_album.tracks):
            return self._current_album.tracks[self._current_track_idx]
        return None

    def action_toggle_fullscreen(self) -> None:
        """`f` — toggle the fullscreen visualizer.

        Sizing is governed entirely by the CSS class rule
        (`Screen.fullscreen Visualizer { height: 1fr; max-height: 100vh; }`)
        — no inline-style overrides. The previous belt-and-suspenders
        approach set `styles.height = '1fr'` inline, which won the
        height race but left the base `max-height: 14` cap in place
        because the inline didn't touch max-height. Net effect: pressing
        `f` hid the sidebar / tracklist but the visualizer stayed at
        14 lines, leaving the bottom of the screen empty.
        """
        viz = self.query_one(Visualizer)
        # Defensive cleanup of any inline override left by an older
        # build that may have stamped one on. Lets the CSS rule win.
        viz.styles.clear_rule("height")
        viz.styles.clear_rule("max_height")
        if self.screen.has_class("fullscreen"):
            self.screen.remove_class("fullscreen")
            # Hiding the focused widget via CSS drops focus; put it back
            # where it was before fullscreen so the user lands on the
            # TrackList they were last interacting with, not on whatever
            # focusable widget Textual fell back to (BrowserList).
            saved = self._focus_before_fullscreen
            self._focus_before_fullscreen = None
            if saved is not None:
                try:
                    saved.focus()
                except Exception:  # pragma: no cover — widget may have been removed
                    pass
        else:
            self._focus_before_fullscreen = self.focused
            self.screen.add_class("fullscreen")

    def action_toggle_help(self) -> None:
        """`?` shows / hides Textual's full keybindings help panel."""
        from textual.widgets import HelpPanel

        try:
            panel = self.query_one(HelpPanel)
        except Exception:
            self.action_show_help_panel()
        else:
            panel.remove()

    def action_airplay_picker(self) -> None:
        """`a` opens the AirPlay device picker (lazy-init pyatv on first open)."""
        from musickit.tui.airplay_picker import AirPlayPickerScreen

        self.push_screen(AirPlayPickerScreen(self))

    def action_edit_tags(self) -> None:
        """`e` opens a tag editor for the currently-focused row.

        - BrowserList focused on an album row → album-wide editor (album,
          album-artist, year, genre across every track).
        - TrackList focused on a track → per-track editor (title, artist,
          track #, etc.).

        Refused in Subsonic-client mode — the synthetic `/subsonic/...`
        paths aren't real on-disk files.
        """
        if self._subsonic_client is not None:
            self.notify("Tag editing isn't available in Subsonic-client mode.", severity="warning")
            return
        focused = self.focused
        if isinstance(focused, BrowserList):
            self._open_album_editor_from_browser(focused)
            return
        if isinstance(focused, TrackList):
            self._open_track_editor_from_tracklist(focused)
            return

    def _open_album_editor_from_browser(self, browser: BrowserList) -> None:
        from musickit.tui.tag_editor import AlbumTagEditorScreen

        highlighted = browser.highlighted_child
        if highlighted is None:
            return
        kind = getattr(highlighted, "entry_kind", None)
        data = getattr(highlighted, "entry_data", None)
        if kind != "album" or not isinstance(data, library_mod.LibraryAlbum):
            self.notify("Highlight an album row to edit album-wide tags.", severity="warning")
            return
        self.push_screen(AlbumTagEditorScreen(self, data))

    def _open_track_editor_from_tracklist(self, tracklist: TrackList) -> None:
        if self._in_radio_view:
            self.notify("Tag editing only applies to library tracks.", severity="warning")
            return
        if self._current_album is None:
            return
        highlighted = tracklist.highlighted_child
        if highlighted is None:
            return
        track_idx = getattr(highlighted, "track_index", None)
        if track_idx is None or not (0 <= track_idx < len(self._current_album.tracks)):
            return
        track = self._current_album.tracks[track_idx]
        from musickit.tui.tag_editor import TrackTagEditorScreen

        self.push_screen(TrackTagEditorScreen(self, track))

    def notify_track_tags_updated(self, track: LibraryTrack) -> None:
        """Called by the tag editor after a successful save.

        Re-renders the tracklist row so the new title / artist appear without
        a full library rescan. Also refreshes NowPlayingMeta if the edited
        track is the one playing.
        """
        if self._current_album is None:
            return
        try:
            idx = self._current_album.tracks.index(track)
        except ValueError:
            return
        # Re-render just this row.
        try:
            tracklist = self.query_one(TrackList)
        except NoMatches:
            return
        if 0 <= idx < len(tracklist.children):
            item = tracklist.children[idx]
            if isinstance(item, ListItem):
                rows = item.query(Static)
                if rows:
                    from musickit.tui.formatters import compute_title_width

                    is_playing = idx == self._current_track_idx
                    title_width = compute_title_width(tracklist.size.width, header_padding=2)
                    rows.first().update(
                        format_track_row(idx, track, self._current_album, marker=is_playing, title_width=title_width)
                    )
        if idx == self._current_track_idx:
            self._refresh_status()
        self.notify(f"✓ Tags saved: {track.path.name}", severity="information")

    def notify_album_tags_updated(self, album: LibraryAlbum) -> None:
        """Called by the album editor after a successful album-wide save.

        Repaints the tracklist (year/genre/album columns may have changed)
        and the now-playing block if a track from this album is playing.
        Doesn't trigger a full library rescan — the in-memory model has
        already been patched by the editor.
        """
        if self._current_album is album:
            self._repopulate_playlist()
        if self._current_album is album and self._current_track_idx is not None:
            self._refresh_status()
        self.notify(
            f"✓ Album tags saved across {len(album.tracks)} track(s): {album.album_dir}",
            severity="information",
        )

    def action_start_filter(self) -> None:
        """`/`: open a filter input above the focused pane (browser or tracklist)."""
        focused = self.focused
        anchor: Widget
        if isinstance(focused, BrowserList):
            target_id = "browser"
            parent = self.query_one("#sidebar", Vertical)
            anchor = self.query_one(BrowserList)
        elif isinstance(focused, TrackList):
            target_id = "tracklist"
            parent = self.query_one("#main", Vertical)
            # Mount above the scroll wrapper, not inside it.
            anchor = self.query_one("#track-scroll", VerticalScroll)
        else:
            return  # `/` is a no-op when neither list is focused
        # If a filter is already open, just refocus it.
        existing = list(self.query(FilterInput))
        if existing:
            existing[0].focus()
            return
        inp = FilterInput(placeholder=f"filter {target_id}…", id=f"filter-{target_id}")
        inp.target_pane = target_id  # type: ignore[attr-defined]
        parent.mount(inp, before=anchor)
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-narrow the targeted pane as the user types."""
        if not isinstance(event.input, FilterInput):
            return
        target = getattr(event.input, "target_pane", None)
        if target == "browser":
            self._browser_filter = event.value
            self._populate_browser()
        elif target == "tracklist":
            self._tracklist_filter = event.value
            if self._in_radio_view:
                self._repopulate_radio_playlist()
            else:
                self._repopulate_playlist()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter on the filter input → dismiss the input, keep the filter active.

        The narrowed list remains; the user can navigate it with arrows + Enter
        to play. To clear the filter entirely, press `/` again and Esc, or
        navigate to a different pane (which auto-clears).
        """
        if not isinstance(event.input, FilterInput):
            return
        target = getattr(event.input, "target_pane", None)
        event.input.remove()
        pane = self.query_one(BrowserList if target == "browser" else TrackList)
        pane.focus()
        event.stop()

    async def on_key(self, event: events.Key) -> None:
        """Esc on a focused FilterInput dismisses the filter + restores the list."""
        if event.key != "escape":
            return
        if not isinstance(self.focused, FilterInput):
            return
        target = getattr(self.focused, "target_pane", None)
        self._dismiss_filter(target)
        event.stop()

    def _dismiss_filter(self, target: str | None) -> None:
        """Remove the filter input, clear its filter string, repopulate, refocus."""
        for inp in list(self.query(FilterInput)):
            inp.remove()
        if target == "browser":
            self._browser_filter = ""
            self._populate_browser()
            self.query_one(BrowserList).focus()
        elif target == "tracklist":
            self._tracklist_filter = ""
            if self._in_radio_view:
                self._repopulate_radio_playlist()
            else:
                self._repopulate_playlist()
            self.query_one(TrackList).focus()

    def _clear_browser_filter(self) -> None:
        """Drop any active browser filter + dismiss its input. Used on pane navigation."""
        if not self._browser_filter and not self.query(FilterInput):
            return
        self._browser_filter = ""
        for inp in list(self.query("#filter-browser")):
            inp.remove()

    def _clear_tracklist_filter(self) -> None:
        """Drop any active tracklist filter + dismiss its input. Used on pane navigation."""
        if not self._tracklist_filter and not self.query(FilterInput):
            return
        self._tracklist_filter = ""
        for inp in list(self.query("#filter-tracklist")):
            inp.remove()

    @property
    def airplay(self) -> AirPlayController | None:
        """Currently configured AirPlay controller (or None for local-only)."""
        return self._airplay

    def get_or_create_airplay(self) -> AirPlayController:
        """Return the AirPlay controller, lazy-initialising on first use.

        The controller spawns a background asyncio loop thread, so we avoid
        creating one until the user actually opens the picker. Once created
        it sticks around for the rest of the session and gets cleaned up
        in `on_unmount`.

        Does NOT wire the controller into the audio player — that happens
        in `switch_airplay` once the user actually picks a device. Wiring
        here would call `player.set_airplay(...)` which calls `stop()`,
        interrupting current playback the moment the picker opens.
        """
        if self._airplay is None:
            from musickit.tui.airplay import AirPlayController

            self._airplay = AirPlayController()
        return self._airplay

    def switch_airplay(self, device: AirPlayDevice | None) -> None:
        """Connect / disconnect the AirPlay output. `None` → play locally.

        Persists the choice to state.toml so the next launch resumes it.
        Stops any in-flight playback so the next track starts on the new
        target cleanly.
        """
        controller = self.get_or_create_airplay()
        if device is None:
            try:
                controller.detach()
            except Exception:  # pragma: no cover — best-effort
                pass
            self._player.set_airplay(None)
            _save_airplay_state(None)
            return
        controller.connect(device)
        self._player.set_airplay(controller)
        _save_airplay_state(device)

    # ------------------------------------------------------------------
    # Async library scan
    # ------------------------------------------------------------------

    def _show_scan_overlay(self, body: str) -> None:
        overlay = self.query_one(ScanOverlay)
        overlay.body = body
        overlay.add_class("visible")
        # Hide everything else while scanning so the centered card is the
        # only thing on screen.
        self.screen.add_class("scanning")

    def _hide_scan_overlay(self) -> None:
        overlay = self.query_one(ScanOverlay)
        overlay.remove_class("visible")
        self.screen.remove_class("scanning")

    @work(thread=True, exclusive=True, group="scan")
    def _scan_library_async(self, *, initial: bool) -> None:
        prior_artist = self._browse_artist

        if self._subsonic_client is not None:
            from musickit.tui.subsonic_client import SubsonicError, build_index

            def on_subsonic_album(name: str, idx: int, total: int) -> None:
                self.call_from_thread(self._on_subsonic_scan_progress, name, idx, total)

            try:
                new_index = build_index(self._subsonic_client, on_progress=on_subsonic_album)
            except SubsonicError as exc:
                log.warning("subsonic walk failed: %s", exc)
                self.call_from_thread(self._on_scan_failed, str(exc))
                return
            self.call_from_thread(self._on_scan_complete, new_index, prior_artist, initial)
            return

        if self._root is None:
            return
        root = self._root

        def on_album(album_dir: Path, idx: int, total: int) -> None:
            self.call_from_thread(self._on_scan_progress, album_dir, idx, total)

        force = self._pending_force_rescan
        # The --full-rescan CLI flag fires only once, on the first scan.
        # Subsequent Ctrl+R rescans go through the delta-validate path.
        self._pending_force_rescan = False
        new_index = library_mod.load_or_scan(
            root,
            use_cache=self._use_cache,
            force=force,
            on_album=on_album,
        )
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

    def _on_subsonic_scan_progress(self, name: str, idx: int, total: int) -> None:
        overlay = self.query_one(ScanOverlay)
        if len(name) > 50:
            name = name[:49] + "…"
        bar_width = 30
        ratio = idx / max(1, total)
        filled = int(round(ratio * bar_width))
        bar = f"[{C_PLAYING}]{'█' * filled}[/][{C_DIM}]{'░' * (bar_width - filled)}[/]"
        overlay.body = f"[bold cyan]Loading library from server…[/]\n\n{bar}\n[dim]{idx} / {total}[/]\n\n[dim]{name}[/]"

    def _on_scan_failed(self, message: str) -> None:
        """Stop the scan overlay and surface the error inline."""
        overlay = self.query_one(ScanOverlay)
        overlay.body = f"[bold red]Scan failed[/]\n\n[dim]{message}[/]\n\n[dim]Press q to quit[/]"

    def _on_scan_complete(self, new_index: LibraryIndex, prior_artist: str | None, initial: bool) -> None:
        del initial
        self._index = new_index
        if prior_artist is not None and not any(a.artist_dir == prior_artist for a in self._index.albums):
            self._browse_artist = None
        self._populate_sidebar_stats()
        self._populate_browser()
        self._hide_scan_overlay()

    # ------------------------------------------------------------------
    # Playback orchestration
    # ------------------------------------------------------------------

    def _play_current(self) -> None:
        track = self._currently_playing_track()
        if track is None:
            return
        # In Subsonic-client mode `track.stream_url` is set and `track.path`
        # is a synthetic placeholder. AudioPlayer accepts URL strings (it's
        # what radio uses already), so the same call works for both.
        source: Path | str = track.stream_url if track.stream_url else track.path
        # Forward ReplayGain tags so the player can apply gain. Subsonic-
        # client mode tracks don't carry RG (Subsonic's API doesn't expose
        # it) — the dict is empty and the multiplier resolves to 1.0.
        self._player.play(source, replaygain=track.replaygain or {})
        self._refresh_play_marker()

    def _advance_track(self, *, force: bool = False) -> None:
        if self._current_album is None or self._current_track_idx is None:
            return
        next_idx = compute_next_track_idx(
            current_idx=self._current_track_idx,
            track_count=len(self._current_album.tracks),
            shuffle=self._shuffle,
            repeat=self._repeat,
            force=force,
        )
        if next_idx is None:
            self._player.stop()
            self._current_track_idx = None
            self._refresh_play_marker()
            return
        self._current_track_idx = next_idx
        self._play_current()

    # ------------------------------------------------------------------
    # Player callbacks (run on background threads)
    # ------------------------------------------------------------------

    def _on_track_end(self) -> None:
        self._end_pending = True

    def _on_track_failed(self, path: Path | str, message: str) -> None:
        log.warning("track failed: %s — %s", path, message)
        self._end_pending = True


def _save_airplay_state(device: AirPlayDevice | None) -> None:
    """Persist (or clear) the AirPlay device choice in `state.toml`."""
    state = load_state()
    if device is None:
        state.pop("airplay", None)
    else:
        state["airplay"] = {
            "name": device.name,
            "identifier": device.identifier,
            "address": device.address,
        }
    save_state(state)
