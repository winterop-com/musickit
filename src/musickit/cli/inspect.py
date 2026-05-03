"""`musickit inspect` — dump tags + embedded picture info for one audio file."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit.cli import app
from musickit.metadata import read_source


@app.command()
def inspect(path: Annotated[Path, typer.Argument(help="Audio file to summarize.")]) -> None:
    """Dump the tags + embedded picture info for one audio file."""
    console = Console()
    track = read_source(path)
    console.print_json(track.model_dump_json(exclude={"embedded_picture"}))
    if track.embedded_picture:
        console.print(
            f"[dim]embedded picture: {len(track.embedded_picture)} bytes, "
            f"{track.embedded_picture_mime}, ~{track.embedded_picture_pixels} px[/dim]"
        )
