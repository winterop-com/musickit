"""`musickit inspect` — pretty-print tags + embedded picture info for one audio file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.box import SIMPLE_HEAVY
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from musickit.cli import app
from musickit.metadata import read_source
from musickit.metadata.models import SourceTrack


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Audio file to summarize.")],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit raw JSON instead of the pretty layout."),
    ] = False,
) -> None:
    """Dump the tags + embedded picture info for one audio file."""
    console = Console()
    track = read_source(path)

    if json_out:
        console.print_json(track.model_dump_json(exclude={"embedded_picture"}))
        return

    file_panel = _render_file_panel(track, path)
    tags_panel = _render_tags_panel(track)
    extras: list[Any] = []
    if track.replaygain:
        extras.append(_render_replaygain_panel(track))
    if track.lyrics:
        extras.append(_render_lyrics_panel(track))
    if track.embedded_picture is not None:
        extras.append(_render_picture_panel(track))

    panels: list[Any] = [file_panel, tags_panel, *extras]
    console.print(Group(*panels))


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


def _render_file_panel(track: SourceTrack, path: Path) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("path", str(path.resolve()))
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    table.add_row("size", _format_size(size))
    table.add_row("format", path.suffix.lstrip(".").upper() or "unknown")
    if track.duration_s is not None:
        table.add_row("duration", _format_duration(track.duration_s))
    if track.mb_recording_id:
        table.add_row("mb recording id", track.mb_recording_id)
    return Panel(table, title="[bold]File[/bold]", border_style="cyan", box=SIMPLE_HEAVY)


def _render_tags_panel(track: SourceTrack) -> Panel:
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()

    def add(label: str, value: object) -> None:
        if value is None or value == "" or value == [] or value == {}:
            return
        table.add_row(label, _format_value(value))

    add("title", track.title)
    add("artist", track.artist)
    add("album artist", track.album_artist)
    add("album", track.album)
    add("date", track.date)
    add("track", _format_number(track.track_no, track.track_total))
    add("disc", _format_number(track.disc_no, track.disc_total))
    if track.genres:
        add("genres", ", ".join(track.genres))
    elif track.genre:
        add("genre", track.genre)
    add("bpm", track.bpm)
    add("label", track.label)
    add("catalog", track.catalog)

    if not table.row_count:
        return Panel(
            Text("(no tags found)", style="yellow"), title="[bold]Tags[/bold]", border_style="cyan", box=SIMPLE_HEAVY
        )
    return Panel(table, title="[bold]Tags[/bold]", border_style="cyan", box=SIMPLE_HEAVY)


def _render_replaygain_panel(track: SourceTrack) -> Panel:
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for key in sorted(track.replaygain):
        table.add_row(key, track.replaygain[key])
    return Panel(table, title="[bold]ReplayGain[/bold]", border_style="cyan", box=SIMPLE_HEAVY)


def _render_lyrics_panel(track: SourceTrack) -> Panel:
    lyrics = track.lyrics or ""
    snippet = lyrics if len(lyrics) <= 400 else lyrics[:400].rstrip() + "  …"
    line_count = lyrics.count("\n") + 1 if lyrics else 0
    body = Group(
        Text(f"{len(lyrics)} chars · {line_count} line(s)", style="dim"),
        Text(""),
        Text(snippet),
    )
    return Panel(body, title="[bold]Lyrics[/bold]", border_style="cyan", box=SIMPLE_HEAVY)


def _render_picture_panel(track: SourceTrack) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    size_bytes = len(track.embedded_picture) if track.embedded_picture else 0
    table.add_row("mime", track.embedded_picture_mime or "?")
    table.add_row("size", _format_size(size_bytes))
    if track.embedded_picture_pixels:
        table.add_row("pixels", f"~{track.embedded_picture_pixels:,} px")
    return Panel(table, title="[bold]Embedded picture[/bold]", border_style="cyan", box=SIMPLE_HEAVY)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _format_number(n: int | None, total: int | None) -> str | None:
    if n is None and total is None:
        return None
    if n is None:
        return f"?/{total}"
    if total is None:
        return str(n)
    return f"{n}/{total}"


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if f < 1024:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TiB"
