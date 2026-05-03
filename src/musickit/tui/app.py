"""Textual app: cliamp-styled now-playing + library tree + playlist."""

from __future__ import annotations

import logging
import random
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, ListItem, ListView, Static, Tree

from musickit import library as library_mod
from musickit.tui.player import AudioPlayer

if TYPE_CHECKING:
    from musickit.library import LibraryAlbum, LibraryIndex, LibraryTrack

log = logging.getLogger(__name__)


class RepeatMode(str, Enum):
    """Cycle target for the `r` keybinding."""

    OFF = "Off"
    ALBUM = "Album"
    TRACK = "Track"


class HeaderBlock(Static):
    """Top status block: app name, current track, time, state, volume.

    In fullscreen mode (`Screen.fullscreen`), this widget grows and renders
    the now-playing info in a larger / centered layout.
    """

    DEFAULT_CSS = """
    HeaderBlock {
        height: 4;
        padding: 0 1;
        background: $boost;
    }
    Screen.fullscreen HeaderBlock {
        height: 8;
        padding: 1 2;
    }
    """

    title_line = reactive("")
    time_line = reactive("00:00 / 00:00")
    state_badge = reactive("⏹ Stopped")
    volume_line = reactive("VOL " + "░" * 20 + "  100%")
    progress_line = reactive("")
    fullscreen_now_playing = reactive("")

    def render(self) -> str:
        if self.has_class("fullscreen"):
            return self.fullscreen_now_playing
        return (
            f"[bold #ff69b4]musickit[/]   {self.title_line}\n"
            f"[#ffd700]{self.time_line:<40}[/]{self.state_badge:>40}\n"
            f"{self.volume_line}"
        )


class Visualizer(Static):
    """8-band spectrum bars driven by the audio callback's FFT output.

    Each band gets its own hue (rainbow across the spectrum). Rows blend
    intensity top-to-bottom, with a bright "peak" highlight on the topmost
    active row of each bar.
    """

    DEFAULT_CSS = """
    Visualizer {
        height: 7;
        padding: 1 1;
        background: $boost;
    }
    Screen.fullscreen Visualizer {
        height: 1fr;
    }
    """

    # Rainbow per-band base colors (low freq → high freq).
    _BAND_COLORS = (
        "#ff5f5f",  # red
        "#ff8c00",  # orange
        "#ffd700",  # gold
        "#7fff00",  # chartreuse
        "#00ced1",  # turquoise
        "#1e90ff",  # dodger blue
        "#9370db",  # medium purple
        "#ff69b4",  # hot pink
    )

    levels = reactive([0.0] * 8)

    def render(self) -> str:
        rows = max(5, max(0, self.size.height - 2))
        lines: list[str] = []
        for row_idx in range(rows):
            # Row 0 = top (peak), row rows-1 = bottom.
            threshold = 1.0 - (row_idx + 1) / rows
            line_parts: list[str] = []
            for band_idx, level in enumerate(self.levels):
                base = self._BAND_COLORS[band_idx % len(self._BAND_COLORS)]
                if level >= threshold:
                    # Topmost active row gets a "peak" highlight in white,
                    # rest of the bar gets the band's hue.
                    is_peak_row = (1.0 - level) <= (row_idx / rows) < (1.0 - level) + (1.0 / rows)
                    color = "bold white" if is_peak_row else base
                    line_parts.append(f"[{color}]████[/]")
                else:
                    line_parts.append("    ")
                line_parts.append(" ")  # gap
            lines.append("".join(line_parts))
        return "\n".join(lines)


class PlaylistHeader(Static):
    """Static rule above the track list (shows shuffle/repeat/cursor state)."""

    DEFAULT_CSS = """
    PlaylistHeader {
        height: auto;
        padding: 0 1;
        color: $primary;
    }
    """

    body = reactive("[dim]Select an album in the library to populate the playlist.[/dim]")

    def render(self) -> str:
        return self.body


class TrackList(ListView):
    """Focusable playlist. Enter on a row plays that track."""

    DEFAULT_CSS = """
    TrackList {
        padding: 0 1;
        height: 1fr;
    }
    """


_TREE_DEFAULT_WIDTH = 36
_TREE_MIN_WIDTH = 16
_TREE_MAX_WIDTH = 80
_TREE_RESIZE_STEP = 4


class LibraryTree(Tree[object]):
    """Left-hand artist/album tree (resize via `Ctrl+←` / `Ctrl+→`)."""

    DEFAULT_CSS = """
    LibraryTree {
        width: 36;
        border-right: solid $primary;
    }
    """


class MusickitApp(App[None]):
    """3-row Textual app: header / library | playlist / footer."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    #playlist-pane {
        width: 1fr;
        padding: 0 1;
    }
    Screen.fullscreen #body {
        display: none;
    }
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", show=True),
        Binding("space", "toggle_pause", "Play/Pause", show=True),
        Binding("enter", "play_selected", "Play", show=True),
        Binding("n", "next_track", "Next", show=True),
        Binding("p", "prev_track", "Prev", show=True),
        Binding("plus,equals_sign", "vol_up", "Vol+", show=False),
        Binding("minus", "vol_down", "Vol-", show=False),
        Binding("left", "seek_back", "Seek -", show=True),
        Binding("right", "seek_fwd", "Seek +", show=True),
        Binding("s", "toggle_shuffle", "Shuffle", show=True),
        Binding("r", "cycle_repeat", "Repeat", show=True),
        Binding("f", "toggle_fullscreen", "Fullscreen", show=True),
        Binding("tab", "focus_next", "Focus", show=True),
        Binding("ctrl+left", "tree_narrower", "Tree-", show=True),
        Binding("ctrl+right", "tree_wider", "Tree+", show=True),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._index: LibraryIndex | None = None
        self._player = AudioPlayer()
        self._player.on_track_end = self._on_track_end
        self._player.on_track_failed = self._on_track_failed
        self._current_album: LibraryAlbum | None = None
        self._current_track_idx: int | None = None
        # Last index that visibly carries the `▶` marker — needed so we can
        # clear it without a full rebuild when the marker moves to a new row.
        self._marker_idx: int | None = None
        self._shuffle = False
        self._repeat = RepeatMode.OFF
        self._end_pending = False  # set by callback thread, drained by UI tick

    def compose(self) -> ComposeResult:
        yield HeaderBlock(id="header")
        yield Visualizer(id="visualizer")
        with Horizontal(id="body"):
            yield LibraryTree("Library", id="tree")
            with VerticalScroll(id="playlist-pane"):
                yield PlaylistHeader(id="playlist-header")
                yield TrackList(id="tracklist")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "musickit"
        self._index = library_mod.scan(self._root)
        library_mod.audit(self._index)
        self._populate_tree()
        self.set_interval(0.1, self._refresh_status)
        self.set_interval(0.05, self._drain_end_pending)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def _populate_tree(self) -> None:
        tree = self.query_one(LibraryTree)
        tree.root.expand()
        if self._index is None or not self._index.albums:
            tree.root.add_leaf("(no albums)")
            return
        by_artist: dict[str, list[LibraryAlbum]] = {}
        for album in self._index.albums:
            by_artist.setdefault(album.artist_dir, []).append(album)
        for artist in sorted(by_artist, key=str.lower):
            artist_node = tree.root.add(artist, expand=False)
            for album in by_artist[artist]:
                warn = " ⚠" if album.warnings else ""
                artist_node.add_leaf(f"{album.album_dir}{warn}", data=album)

    def on_tree_node_selected(self, event: Tree.NodeSelected[object]) -> None:
        data = event.node.data
        if isinstance(data, library_mod.LibraryAlbum):
            self._set_current_album(data, track_idx=None)
            self._repopulate_playlist()
            # Hand focus to the track list so Enter plays the track right away.
            tracklist = self.query_one(TrackList)
            tracklist.focus()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[object]) -> None:
        data = event.node.data
        if isinstance(data, library_mod.LibraryAlbum) and self._current_album is not data:
            self._set_current_album(data, track_idx=None)
            self._repopulate_playlist()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a track row plays that track."""
        idx = getattr(event.item, "track_index", None)
        if idx is not None and isinstance(idx, int):
            self._current_track_idx = idx
            self._play_current()

    # ------------------------------------------------------------------
    # Playlist rendering
    # ------------------------------------------------------------------

    def _set_current_album(self, album: LibraryAlbum, *, track_idx: int | None) -> None:
        self._current_album = album
        self._current_track_idx = track_idx

    def _repopulate_playlist(self) -> None:
        """Full rebuild — only call when the album changes.

        Track-change-within-same-album should call `_refresh_play_marker`
        instead so we don't clear+rebuild the whole list (visible flash).
        """
        header = self.query_one(PlaylistHeader)
        tracklist = self.query_one(TrackList)
        if self._current_album is None:
            header.body = "[dim]Select an album in the library to populate the playlist.[/dim]"
            tracklist.clear()
            self._marker_idx = None
            return
        self._update_playlist_header()
        prev_cursor = tracklist.index
        tracklist.clear()
        album = self._current_album
        for i, track in enumerate(album.tracks):
            label = self._format_track_row(i, track, album, marker=(i == self._current_track_idx))
            item = ListItem(Static(label, id=f"track-row-{i}"))
            item.track_index = i  # type: ignore[attr-defined]
            tracklist.append(item)
        self._marker_idx = self._current_track_idx
        if prev_cursor is not None and 0 <= prev_cursor < len(album.tracks):
            tracklist.index = prev_cursor

    def _refresh_play_marker(self) -> None:
        """Move the `▶` marker to `self._current_track_idx` without rebuilding.

        Touches only the previously-marked row and the new row's Static labels.
        Eliminates the full-rebuild flash when starting a new track in the
        same album (mouse click was the most visible offender).
        """
        if self._current_album is None:
            return
        album = self._current_album
        old, new = self._marker_idx, self._current_track_idx
        if old == new:
            self._update_playlist_header()
            return
        if old is not None and 0 <= old < len(album.tracks):
            self._update_track_row_label(old, marker=False)
        if new is not None and 0 <= new < len(album.tracks):
            self._update_track_row_label(new, marker=True)
        self._marker_idx = new
        self._update_playlist_header()

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
        glyph = "[bold #7fff00]▶[/]" if marker else " "
        artist = track.artist or album.artist_dir
        title = track.title or track.path.stem
        if marker:
            return f"{glyph} [bold #ffd700]{idx + 1:>2}.[/]  [#7fff00]{artist}[/] [#9370db]-[/] [bold]{title}[/]"
        return f"{glyph} [#888888]{idx + 1:>2}.[/]  [#bbbbbb]{artist}[/] [#666666]-[/] {title}"

    def _update_playlist_header(self) -> None:
        if self._current_album is None:
            return
        header = self.query_one(PlaylistHeader)
        album = self._current_album
        shuffle_color = "#7fff00" if self._shuffle else "#666666"
        repeat_color = "#7fff00" if self._repeat is not RepeatMode.OFF else "#666666"
        idx_label = (
            f"{(self._current_track_idx or 0) + 1}/{len(album.tracks)}"
            if self._current_track_idx is not None
            else f"-/{len(album.tracks)}"
        )
        header.body = (
            f"[bold #ff69b4]── Playlist ──[/]  "
            f"[{shuffle_color}]Shuffle: {'On' if self._shuffle else 'Off'}[/]  "
            f"[{repeat_color}]Repeat: {self._repeat.value}[/]  "
            f"[#ffd700][{idx_label}][/]\n"
            f"[bold #00ced1]── {album.album_dir} ──[/]"
        )

    # ------------------------------------------------------------------
    # Header / status refresh
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        header = self.query_one(HeaderBlock)
        visualizer = self.query_one(Visualizer)
        track = self._currently_playing_track()
        if track is None:
            header.title_line = "[dim]nothing queued[/dim]"
            header.time_line = "00:00 / 00:00"
            header.state_badge = "⏹ Stopped"
            header.fullscreen_now_playing = "[dim]nothing queued — press f to exit[/dim]"
        else:
            artist = track.artist or "?"
            title = track.title or track.path.stem
            album = self._current_album.album_dir if self._current_album else ""
            time_str = f"{_fmt_mmss(self._player.position)} / {_fmt_mmss(self._player.duration)}"
            header.title_line = (
                f"[#00ced1]♪[/] [bold #ffffff]{title}[/] "
                f"[#9370db]·[/] [#7fff00]{artist}[/] "
                f"[#9370db]·[/] [dim]{album}[/dim]"
            )
            header.time_line = time_str
            if self._player.is_paused:
                state = "[bold #ffd700]⏸ Paused[/]"
            elif self._player.is_playing:
                state = "[bold #7fff00]▶ Playing[/]"
            else:
                state = "[dim]⏹ Stopped[/dim]"
            header.state_badge = state
            progress = _progress_bar(self._player.position, self._player.duration, width=60)
            header.fullscreen_now_playing = (
                f"[bold #00ced1]♪[/]  [bold #ffffff]{title}[/]\n"
                f"   [#7fff00]{artist}[/]\n"
                f"   [dim]{album}[/dim]\n\n"
                f"   {progress}\n"
                f"   [#ffd700]{time_str:<60}[/]{state:>20}"
            )
        header.volume_line = _volume_bar(self._player.volume)
        visualizer.levels = self._player.band_levels

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
    # Actions / bindings
    # ------------------------------------------------------------------

    def action_play_selected(self) -> None:
        """Play whatever is highlighted (track row or album node)."""
        tracklist = self.query_one(TrackList)
        tree = self.query_one(LibraryTree)
        focused = self.focused
        if focused is tracklist and tracklist.highlighted_child is not None:
            idx = getattr(tracklist.highlighted_child, "track_index", None)
            if isinstance(idx, int):
                self._current_track_idx = idx
                self._play_current()
                return
        node = tree.cursor_node
        if node is not None and isinstance(node.data, library_mod.LibraryAlbum):
            self._set_current_album(node.data, track_idx=0)
            self._play_current()
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

    def action_vol_up(self) -> None:
        self._player.set_volume(min(100, self._player.volume + 5))

    def action_vol_down(self) -> None:
        self._player.set_volume(max(0, self._player.volume - 5))

    def action_toggle_shuffle(self) -> None:
        self._shuffle = not self._shuffle
        self._update_playlist_header()

    def action_cycle_repeat(self) -> None:
        order = [RepeatMode.OFF, RepeatMode.ALBUM, RepeatMode.TRACK]
        self._repeat = order[(order.index(self._repeat) + 1) % len(order)]
        self._update_playlist_header()

    def action_tree_wider(self) -> None:
        self._resize_tree(_TREE_RESIZE_STEP)

    def action_tree_narrower(self) -> None:
        self._resize_tree(-_TREE_RESIZE_STEP)

    def action_toggle_fullscreen(self) -> None:
        """Hide the library / playlist and let the visualizer fill the screen."""
        header = self.query_one(HeaderBlock)
        visualizer = self.query_one(Visualizer)
        body = self.query_one("#body")
        going_fullscreen = not header.has_class("fullscreen")
        if going_fullscreen:
            header.add_class("fullscreen")
            header.styles.height = 8
            visualizer.styles.height = "1fr"
            body.styles.display = "none"
        else:
            header.remove_class("fullscreen")
            header.styles.height = 4
            visualizer.styles.height = 7
            body.styles.display = "block"

    def _resize_tree(self, delta: int) -> None:
        tree = self.query_one(LibraryTree)
        # Tree.styles.width is a Scalar; pull the integer cell value, clamp, set.
        current = tree.styles.width
        try:
            current_cells = int(current.value) if current is not None else _TREE_DEFAULT_WIDTH
        except (TypeError, ValueError):
            current_cells = _TREE_DEFAULT_WIDTH
        new_width = max(_TREE_MIN_WIDTH, min(_TREE_MAX_WIDTH, current_cells + delta))
        tree.styles.width = new_width

    # ------------------------------------------------------------------
    # Playback orchestration
    # ------------------------------------------------------------------

    def _play_current(self) -> None:
        track = self._currently_playing_track()
        if track is None:
            return
        self._player.play(track.path)
        # Same album → only update markers (no flash). Different album path
        # is handled in `on_tree_node_selected` which already repopulates.
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
        # End of album, no repeat — stop and clear marker.
        self._player.stop()
        self._current_track_idx = None
        self._refresh_play_marker()

    # ------------------------------------------------------------------
    # Player callbacks (run on background threads)
    # ------------------------------------------------------------------

    def _on_track_end(self) -> None:
        self._end_pending = True

    def _on_track_failed(self, path: Path, message: str) -> None:
        log.warning("track failed: %s — %s", path, message)
        self._end_pending = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _volume_bar(volume: int, width: int = 20) -> str:
    filled = int(round(volume / 100.0 * width))
    return (
        f"[bold #ff8c00]VOL[/]  "
        f"[#7fff00]{'█' * filled}[/][#3a3a3a]{'░' * (width - filled)}[/]  "
        f"[bold #ffd700]{volume:3d}%[/]"
    )


def _progress_bar(position: float, duration: float, width: int = 60) -> str:
    """Solid/empty bar showing playback progress."""
    if duration <= 0:
        return f"[#3a3a3a]{'░' * width}[/]"
    ratio = max(0.0, min(1.0, position / duration))
    filled = int(round(ratio * width))
    return f"[#00ced1]{'█' * filled}[/][#3a3a3a]{'░' * (width - filled)}[/]"
