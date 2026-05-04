"""Shared library-scan progress wrapper for CLI commands.

Wraps `library.load_or_scan` with a transient `rich.Progress` spinner (or
per-album lines under `-v`) so users see feedback during multi-second
walks of large libraries on slow drives. Returns an audited
`LibraryIndex` regardless of whether the cache hit, did a delta-validate,
or ran a full rescan.
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
    use_cache: bool = True,
    force: bool = False,
) -> library_mod.LibraryIndex:
    """Walk `root` (cache-aware) with progress feedback. Returns audited `LibraryIndex`.

    `use_cache=False` falls back to in-memory scan with no DB writes.
    `force=True` rebuilds the index from scratch even if the DB exists.
    The progress bar fires only on the slow path (full scan or per-album
    revalidation); cache hits return immediately with no UI flicker.
    """
    if verbose:

        def on_album_verbose(album_dir: Path, idx: int, total: int) -> None:
            try:
                rel: PurePath = album_dir.relative_to(root)
            except ValueError:
                rel = album_dir
            console.print(f"[dim]({idx}/{total})[/] scanning {rel}")

        return library_mod.load_or_scan(
            root,
            use_cache=use_cache,
            force=force,
            on_album=on_album_verbose,
            measure_pictures=measure_pictures,
        )

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

        return library_mod.load_or_scan(
            root,
            use_cache=use_cache,
            force=force,
            on_album=on_album,
            measure_pictures=measure_pictures,
        )
