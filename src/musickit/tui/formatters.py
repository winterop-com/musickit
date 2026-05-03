"""Pure row-formatter functions for the TrackList — track + station rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from musickit.tui.widgets import C_PLAYING, fmt_mmss

if TYPE_CHECKING:
    from musickit.library import LibraryAlbum, LibraryTrack
    from musickit.radio import RadioStation


def format_track_row(idx: int, track: LibraryTrack, album: LibraryAlbum, *, marker: bool) -> str:
    """Render one library track row. `marker=True` gets the ▶ glyph + bold."""
    glyph = f"[bold {C_PLAYING}]▶[/]" if marker else " "
    artist = (track.artist or album.artist_dir)[:26]
    title = (track.title or track.path.stem)[:44]
    time_str = fmt_mmss(track.duration_s) if track.duration_s > 0 else "  —  "
    if marker:
        num = f"[{C_PLAYING}]{idx + 1:>3}[/]"
        artist_cell = f"[{C_PLAYING}]{artist}[/]"
        title_cell = f"[bold]{title}[/]"
    else:
        num = f"[dim]{idx + 1:>3}[/]"
        artist_cell = artist
        title_cell = title
    return f"{num} {glyph} {title_cell:<44}{artist_cell:<26} [dim]{time_str:>6}[/]"


def format_station_row(idx: int, station: RadioStation, *, marker: bool) -> str:
    """Render one radio station row. `marker=True` gets the ▶ glyph + bold."""
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
