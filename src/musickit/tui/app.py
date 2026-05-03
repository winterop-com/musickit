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
    """Top status block: app name, current track, time, state, VU bar, volume."""

    DEFAULT_CSS = """
    HeaderBlock {
        height: 5;
        padding: 0 1;
        background: $boost;
    }
    """

    title_line = reactive("")
    time_line = reactive("00:00 / 00:00")
    state_badge = reactive("⏹ Stopped")
    vu_bars = reactive("")
    volume_line = reactive("VOL " + "░" * 20 + "  100%")

    def render(self) -> str:
        return (
            f"[bold cyan]musickit[/bold cyan]   {self.title_line}\n"
            f"{self.time_line:<40}{self.state_badge:>40}\n"
            f"{self.vu_bars:<40}{self.volume_line:>40}"
        )


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


class LibraryTree(Tree[object]):
    """Left-hand artist/album tree."""

    DEFAULT_CSS = """
    LibraryTree {
        width: 28;
        min-width: 24;
        max-width: 40;
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
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", show=True),
        Binding("space", "toggle_pause", "Play/Pause", show=True),
        Binding("enter", "play_selected", "Play", show=True),
        Binding("greater_than_sign,n", "next_track", "Next", show=False),
        Binding("less_than_sign,p", "prev_track", "Prev", show=False),
        Binding("plus,equals_sign", "vol_up", "Vol+", show=False),
        Binding("minus", "vol_down", "Vol-", show=False),
        Binding("left", "seek_back", "Seek -", show=True),
        Binding("right", "seek_fwd", "Seek +", show=True),
        Binding("s", "toggle_shuffle", "Shuffle", show=True),
        Binding("r", "cycle_repeat", "Repeat", show=True),
        Binding("tab", "focus_next", "Focus", show=True),
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
        self._shuffle = False
        self._repeat = RepeatMode.OFF
        self._end_pending = False  # set by callback thread, drained by UI tick

    def compose(self) -> ComposeResult:
        yield HeaderBlock(id="header")
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
            self._render_playlist()
            # Hand focus to the track list so Enter plays the track right away.
            tracklist = self.query_one(TrackList)
            tracklist.focus()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[object]) -> None:
        data = event.node.data
        if isinstance(data, library_mod.LibraryAlbum) and self._current_album is not data:
            self._set_current_album(data, track_idx=None)
            self._render_playlist()

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

    def _render_playlist(self) -> None:
        header = self.query_one(PlaylistHeader)
        tracklist = self.query_one(TrackList)
        if self._current_album is None:
            header.body = "[dim]Select an album in the library to populate the playlist.[/dim]"
            tracklist.clear()
            return
        album = self._current_album
        shuffle = "On" if self._shuffle else "Off"
        repeat = self._repeat.value
        idx_label = (
            f"{(self._current_track_idx or 0) + 1}/{len(album.tracks)}"
            if self._current_track_idx is not None
            else f"-/{len(album.tracks)}"
        )
        header.body = (
            f"[bold]── Playlist ──[/bold] [Shuffle: {shuffle}] [Repeat: {repeat}] [{idx_label}]\n"
            f"[bold cyan]── {album.album_dir} ──[/bold cyan]"
        )
        # Repopulate the listview. Preserve highlighted index when possible.
        prev_index = tracklist.index
        tracklist.clear()
        for i, track in enumerate(album.tracks):
            marker = "▶" if i == self._current_track_idx else " "
            artist = track.artist or album.artist_dir
            title = track.title or track.path.stem
            label = f"{marker} {i + 1:>2}.  {artist} - {title}"
            item = ListItem(Static(label))
            # Stash the index on the item so the on_list_view_selected handler can recover it.
            item.track_index = i  # type: ignore[attr-defined]
            tracklist.append(item)
        if prev_index is not None and 0 <= prev_index < len(album.tracks):
            tracklist.index = prev_index

    # ------------------------------------------------------------------
    # Header / status refresh
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        header = self.query_one(HeaderBlock)
        track = self._currently_playing_track()
        if track is None:
            header.title_line = "[dim]nothing queued[/dim]"
            header.time_line = "00:00 / 00:00"
            header.state_badge = "⏹ Stopped"
        else:
            artist = track.artist or "?"
            title = track.title or track.path.stem
            album = self._current_album.album_dir if self._current_album else ""
            header.title_line = f"♪ {artist} - {title} · [dim]{album}[/dim]"
            header.time_line = f"{_fmt_mmss(self._player.position)} / {_fmt_mmss(self._player.duration)}"
            if self._player.is_paused:
                header.state_badge = "[yellow]⏸ Paused[/yellow]"
            elif self._player.is_playing:
                header.state_badge = "[green]▶ Playing[/green]"
            else:
                header.state_badge = "[dim]⏹ Stopped[/dim]"
        header.volume_line = _volume_bar(self._player.volume)
        header.vu_bars = _vu_bar(self._player.position, self._player.is_playing)

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
        self._render_playlist()

    def action_cycle_repeat(self) -> None:
        order = [RepeatMode.OFF, RepeatMode.ALBUM, RepeatMode.TRACK]
        self._repeat = order[(order.index(self._repeat) + 1) % len(order)]
        self._render_playlist()

    # ------------------------------------------------------------------
    # Playback orchestration
    # ------------------------------------------------------------------

    def _play_current(self) -> None:
        track = self._currently_playing_track()
        if track is None:
            return
        self._player.play(track.path)
        self._render_playlist()

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
        self._render_playlist()

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
    return f"VOL {'█' * filled}{'░' * (width - filled)}  {volume:3d}%"


def _vu_bar(position: float, playing: bool) -> str:
    """Cosmetic VU bar — driven by position (no real audio analysis in v1)."""
    if not playing:
        return "▁▁▁▁▁▁▁▁"
    # Pseudo-random-but-deterministic-per-frame bar heights.
    seed = int(position * 10) % 8
    chars = "▁▂▃▄▅▆▇█"
    return "".join(chars[(seed + i) % len(chars)] for i in range(8))
