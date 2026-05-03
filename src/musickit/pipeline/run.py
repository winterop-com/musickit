"""Top-level `pipeline.run()` — walks every album under input_root."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from musickit import convert
from musickit import cover as cover_mod
from musickit.convert import DEFAULT_LOSSY_BITRATE, OutputFormat
from musickit.discover import discover_albums
from musickit.pipeline.album import _process_album
from musickit.pipeline.progress import ProgressContext
from musickit.pipeline.report import AlbumReport, _print_summary


def default_workers() -> int:
    """Worker thread default: 2.

    Each worker spawns ffmpeg, which is itself multi-threaded — so even 2
    workers keeps a modern Mac usable for browsing/dev while a big convert
    runs in the background. Bump explicitly with `--workers N` if you don't
    care about foreground responsiveness.
    """
    return 2


def run(
    input_root: Path,
    output_root: Path,
    *,
    fmt: OutputFormat = OutputFormat.AUTO,
    bitrate: str = DEFAULT_LOSSY_BITRATE,
    enrich: bool | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    allow_lossy_recompress: bool = False,
    workers: int | None = None,
    cover_max_edge: int = cover_mod.DEFAULT_MAX_EDGE,
    acoustid_key: str | None = None,
    overwrite: bool = False,
    remove_source: bool = False,
    console: Console | None = None,
) -> list[AlbumReport]:
    """Convert every album under `input_root` into `fmt` under `output_root`.

    `enrich` tri-state: `None` (auto) probes connectivity and enables enrichment
    when MusicBrainz is reachable; `True` forces enrichment regardless (useful
    on flaky networks/proxies that block our TCP probe but allow HTTP); `False`
    disables it entirely.
    Default UI is a two-level rich progress bar (albums + current-album tracks).
    Pass `verbose=True` to swap the bar for one log line per track.
    """
    console = console or Console()
    if not dry_run:
        # `--dry-run` plans only — no encoding, so we don't need ffmpeg on
        # PATH. Useful for previewing what convert will do on a machine that
        # hasn't installed ffmpeg yet.
        convert.ensure_ffmpeg()
    worker_count = max(1, workers if workers is not None else default_workers())

    # Tri-state enrich: None = "auto, probe connectivity"; True = "force on,
    # skip probe"; False = "off". Only the auto case calls is_online so a
    # user who really wants enrichment can bypass a flaky TCP-probe path.
    if enrich is None:
        from musickit.enrich._http import is_online

        if is_online():
            enrich = True
        else:
            console.print(
                "[dim]offline — skipping enrichment (use `--enrich` to force, `--no-enrich` to silence)[/dim]"
            )
            enrich = False

    albums = discover_albums(input_root)
    if not albums:
        console.print(f"[yellow]No albums found under {input_root}")
        return []

    reports: list[AlbumReport] = []
    written_dirs: set[Path] = set()
    if verbose:
        ctx = ProgressContext(verbose=True)
        for album_dir in albums:
            reports.append(
                _process_album(
                    album_dir,
                    output_root,
                    fmt,
                    bitrate,
                    enrich,
                    dry_run,
                    console,
                    ctx,
                    written_dirs,
                    allow_lossy_recompress,
                    worker_count,
                    cover_max_edge,
                    acoustid_key,
                    overwrite,
                    remove_source,
                    input_root,
                )
            )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            albums_task = progress.add_task("[bold]albums", total=len(albums))
            tracks_task = progress.add_task("tracks", total=1, visible=False)
            ctx = ProgressContext(progress=progress, albums_task=albums_task, tracks_task=tracks_task, verbose=False)
            for album_dir in albums:
                reports.append(
                    _process_album(
                        album_dir,
                        output_root,
                        fmt,
                        bitrate,
                        enrich,
                        dry_run,
                        console,
                        ctx,
                        written_dirs,
                        allow_lossy_recompress,
                        worker_count,
                        cover_max_edge,
                        acoustid_key,
                        overwrite,
                        remove_source,
                        input_root,
                    )
                )
                progress.advance(albums_task)

    _print_summary(console, reports)
    return reports
