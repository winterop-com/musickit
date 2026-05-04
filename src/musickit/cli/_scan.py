"""Shared library-scan progress wrapper for CLI commands.

Wraps `library.scan` with a transient `rich.Progress` spinner (or per-album
lines under `-v`) so users see feedback during multi-second walks of large
libraries on slow drives.
"""

from __future__ import annotations

from pathlib import Path, PurePath

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from musickit import library as library_mod


def scan_with_progress(
    console: Console,
    root: Path,
    *,
    verbose: bool = False,
    measure_pictures: bool = False,
    description: str = "[cyan]Scanning library",
) -> library_mod.LibraryIndex:
    """Walk `root` with progress feedback. Returns the populated `LibraryIndex`."""
    if verbose:

        def on_album_verbose(album_dir: Path, idx: int, total: int) -> None:
            try:
                rel: PurePath = album_dir.relative_to(root)
            except ValueError:
                rel = album_dir
            console.print(f"[dim]({idx}/{total})[/] scanning {rel}")

        return library_mod.scan(root, on_album=on_album_verbose, measure_pictures=measure_pictures)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(description, total=None)

        def on_album(album_dir: Path, idx: int, total: int) -> None:
            if progress.tasks[task].total is None:
                progress.update(task, total=total)
            name = album_dir.name
            if len(name) > 40:
                name = name[:39] + "…"
            progress.update(task, advance=1, description=f"{description} [dim]·[/] {name}")

        return library_mod.scan(root, on_album=on_album, measure_pictures=measure_pictures)
