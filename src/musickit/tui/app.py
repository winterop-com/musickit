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

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import ListItem, ListView, Static

from musickit import library as library_mod
from musickit import radio as radio_mod
from musickit.tui.advance import compute_next_track_idx
from musickit.tui.commands import MusickitCommands
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
    BrowserHeader,
    BrowserInfo,
    BrowserList,
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
        Binding("question_mark", "toggle_help", "Help", show=False),
        Binding("a", "airplay_picker", "AirPlay", show=False),
    ]

    def __init__(
        self,
        root: Path | None,
        *,
        subsonic_client: SubsonicClient | None = None,
        airplay: AirPlayController | None = None,
    ) -> None:
        super().__init__()
        self._root: Path | None = root
        self._subsonic_client: SubsonicClient | None = subsonic_client
        self._airplay: AirPlayController | None = airplay
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
        # Set by AudioPlayer.on_metadata_change (worker thread). Drained in
        # the UI tick so the `Now Playing` block updates with new ICY title.
        self._stream_metadata_dirty: bool = False
        self._repeat = RepeatMode.OFF
        self._end_pending = False
        # None = at top level (artists); a string = drilled into that artist.
        self._browse_artist: str | None = None

    def watch_theme(self, theme: str) -> None:
        """Persist theme changes (e.g. via the command palette) to disk."""
        state = load_state()
        if state.get("theme") == theme:
            return
        state["theme"] = theme
        save_state(state)

    async def on_unmount(self) -> None:
        """Close the audio stream + Subsonic httpx pool + AirPlay loop on app exit.

        Without this the process hangs after `q`: PortAudio's C thread holds
        the interpreter alive (Ctrl-C can't reach Python at that point) and
        httpx leaves connection-pool sockets open. Stopping the player closes
        the OutputStream cleanly; closing the client drops the pool;
        disconnecting AirPlay shuts down the background asyncio loop thread.
        """
        try:
            self._player.stop()
        except Exception:  # pragma: no cover — best effort on shutdown
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
        header = self.query_one(BrowserHeader)
        # Invalidate the cursor BEFORE mutating children. Otherwise a stale
        # index from the prior list (e.g. row 32 of a long album list) can
        # leak into the new list and crash the next ↑/↓ keypress.
        browser.index = None
        browser.clear()
        if self._index is None or not self._index.albums:
            header.path = "Browse"
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
            header.path = "Browse"
            self._populate_browser_artists(browser)
        else:
            header.path = f"Browse · [bold]{self._browse_artist}[/]"
            self._populate_browser_albums(browser, self._browse_artist)
        self._fit_sidebar_width()
        target_index = 0
        if self._browse_artist is not None and len(browser.children) > 1:
            target_index = 1
        self.call_after_refresh(self._set_browser_cursor, target_index)

    def _set_browser_cursor(self, target_index: int) -> None:
        """Place the cursor on `target_index` after children mount.

        Also refocuses the browser (Textual sometimes drops focus when a
        widget's children get mass-replaced) and refreshes the info panel
        — `index =` doesn't always fire `Highlighted` post clear+append.
        """
        browser = self.query_one(BrowserList)
        if target_index >= len(browser.children):
            return
        browser.index = target_index
        if not browser.has_focus:
            browser.focus()
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
            idx = getattr(event.item, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        browser = self.query_one(BrowserList)
        if event.list_view is not browser:
            return
        self._update_browser_info(event.item)

    def _handle_browser_selection(self, item: ListItem) -> None:
        kind_first = getattr(item, "entry_kind", None)
        if kind_first == "radio":
            self._open_radio_view()
            self.query_one(TrackList).focus()
            return
        return self._handle_browser_selection_default(item)

    def _open_radio_view(self) -> None:
        """Populate the right-pane track list with the curated radio stations."""
        self._in_radio_view = True
        self._current_album = None
        self._current_track_idx = None
        self._marker_idx = None
        self._repopulate_radio_playlist()

    def _handle_browser_selection_default(self, item: ListItem) -> None:
        kind = getattr(item, "entry_kind", None)
        data = getattr(item, "entry_data", None)
        if kind == "up":
            self._pop_browser_one_level()
        elif kind == "artist" and isinstance(data, str):
            self._browse_artist = data
            self._populate_browser()
        elif kind == "album" and isinstance(data, library_mod.LibraryAlbum):
            self._in_radio_view = False
            self._set_current_album(data, track_idx=None)
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
        self._current_track_idx = track_idx

    def _repopulate_playlist(self) -> None:
        tracklist = self.query_one(TrackList)
        # Same defer-cursor pattern as the browser to avoid the
        # second-album-no-highlight bug.
        tracklist.index = None
        tracklist.clear()
        if self._current_album is None:
            self._marker_idx = None
            return
        album = self._current_album
        for i, track in enumerate(album.tracks):
            label = format_track_row(i, track, album, marker=(i == self._current_track_idx))
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
        tracklist = self.query_one(TrackList)
        tracklist.index = None
        tracklist.clear()
        if not self._radio_stations:
            tracklist.append(ListItem(Static("[dim]No stations configured. Edit `~/.config/musickit/radio.toml`.[/]")))
            return
        for i, station in enumerate(self._radio_stations):
            label = format_station_row(i, station, marker=False)
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            item.station = station  # type: ignore[attr-defined]
            tracklist.append(item)
        self.call_after_refresh(self._set_tracklist_cursor, 0)

    def _play_station(self, station: RadioStation) -> None:
        idx = self._radio_stations.index(station) if station in self._radio_stations else None
        prev = self._marker_idx
        self._current_track_idx = idx
        self._marker_idx = idx
        self._player.play(station.url)
        if prev is not None and prev != idx:
            try:
                self.query_one(f"#track-row-{prev}", Static).update(
                    format_station_row(prev, self._radio_stations[prev], marker=False)
                )
            except Exception:  # pragma: no cover
                pass
        if idx is not None:
            try:
                self.query_one(f"#track-row-{idx}", Static).update(format_station_row(idx, station, marker=True))
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
        if self._current_album is None:
            return
        try:
            label_widget = self.query_one(f"#track-row-{idx}", Static)
        except Exception:
            return
        track = self._current_album.tracks[idx]
        label_widget.update(format_track_row(idx, track, self._current_album, marker=marker))

    # ------------------------------------------------------------------
    # Status refresh (UI ticks)
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
        if self._end_pending:
            self._end_pending = False
            self._advance_track()

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
            # Enter press would be silently swallowed.
            station = getattr(tracklist.highlighted_child, "station", None)
            if isinstance(station, radio_mod.RadioStation):
                self._play_station(station)
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
        self._browse_artist = None
        self._populate_browser()
        if prior_artist is None or self._index is None:
            return
        # Compute prior-artist row index from the data model — `browser.children`
        # can still report stale items mid clear+append. Row 0 is the Radio
        # entry that `_populate_browser_artists` always prepends, so the
        # artist's row is `data_index + 1`.
        artist_names = sorted({a.artist_dir for a in self._index.albums}, key=str.lower)
        try:
            prior_idx = artist_names.index(prior_artist) + 1
        except ValueError:
            return
        self.call_after_refresh(self._set_browser_cursor, prior_idx)

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

    def action_toggle_fullscreen(self) -> None:
        if self.screen.has_class("fullscreen"):
            self.screen.remove_class("fullscreen")
            self.query_one(Visualizer).styles.height = 6
        else:
            self.screen.add_class("fullscreen")
            self.query_one(Visualizer).styles.height = "1fr"

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
        """
        if self._airplay is None:
            from musickit.tui.airplay import AirPlayController

            self._airplay = AirPlayController()
            self._player.set_airplay(self._airplay)
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
