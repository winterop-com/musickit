"""`musickit convert` — re-encode every album under INPUT_DIR into OUTPUT_DIR."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit import pipeline
from musickit.cli import app
from musickit.convert import DEFAULT_LOSSY_BITRATE, OutputFormat, normalize_bitrate
from musickit.cover import DEFAULT_MAX_EDGE as DEFAULT_COVER_MAX_EDGE


@app.command()
def convert(
    ctx: typer.Context,
    input_dir: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Root folder containing albums to convert.",
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            help="Where to write `<Artist>/<YYYY> - <Album>/` folders.",
        ),
    ],
    fmt: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help=(
                "`auto` (default): every track ends up 256k AAC `.m4a` (FLAC/MP3/etc. are "
                "encoded; AAC m4a is stream-copied). `alac` keeps everything bit-perfect; "
                "`aac`/`mp3` force one specific codec."
            ),
        ),
    ] = OutputFormat.AUTO,
    bitrate: Annotated[
        str,
        typer.Option(
            "--bitrate",
            "-b",
            help="Bitrate for lossy formats (e.g. 192k, 256k, 320k). Ignored for ALAC.",
        ),
    ] = DEFAULT_LOSSY_BITRATE,
    enrich: Annotated[
        bool | None,
        typer.Option(
            "--enrich/--no-enrich",
            help=(
                "Look up metadata + higher-res covers online (MusicBrainz + Cover Art Archive). "
                "Default (no flag): on when reachable, auto-skip when offline. "
                "`--enrich` forces it on (skips the connectivity probe). `--no-enrich` forces off."
            ),
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan, but don't touch the filesystem.")] = False,
    verbose_local: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help="Same as the global `-v` — works in either position. Stream a log line per track.",
        ),
    ] = False,
    allow_lossy_recompress: Annotated[
        bool,
        typer.Option(
            "--allow-lossy-recompress",
            help="Allow lossy → lossy transcodes (e.g. MP3 → AAC). Off by default to avoid quality loss.",
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option(
            "--workers",
            "-w",
            min=0,
            help="Parallel track encoders. 0 (default) = 2 workers (keeps the machine usable during a big convert).",
        ),
    ] = 0,
    cover_max_edge: Annotated[
        int,
        typer.Option(
            "--cover-max-edge",
            min=128,
            help=(
                "Maximum cover dimension (px) on the long edge. Default 1000 — "
                "Music.app cover-flow + Finder previews don't need more, and the "
                "savings are ~30% per track."
            ),
        ),
    ] = DEFAULT_COVER_MAX_EDGE,
    acoustid_key: Annotated[
        str,
        typer.Option(
            "--acoustid-key",
            envvar="MUSICKIT_ACOUSTID_KEY",
            help=(
                "AcoustID API key (https://acoustid.org/api-key — free, ~30s registration). "
                "When set, tagless tracks are fingerprinted via `fpcalc` and looked up "
                "against AcoustID to recover title + artist. Requires `chromaprint` "
                "(`brew install chromaprint`) for the fpcalc binary."
            ),
        ),
    ] = "",
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help=(
                "Replace an existing output album dir if its path is already on disk. "
                "Default off — existing albums are preserved (the run merges new albums "
                "alongside, never wipes prior conversions)."
            ),
        ),
    ] = False,
    remove_source: Annotated[
        bool,
        typer.Option(
            "--remove-source/--no-remove-source",
            help=(
                "After each album succeeds, delete its source dir from INPUT_DIR to "
                "free disk space. Per-album, only on success. Refuses to remove the "
                "input root itself."
            ),
        ),
    ] = False,
) -> None:
    """Re-encode every album under INPUT_DIR into OUTPUT_DIR."""
    console = Console()
    try:
        bitrate_norm = normalize_bitrate(bitrate)
    except ValueError as exc:
        # Surface as a typer-formatted parameter error rather than a Python
        # traceback — `--bitrate loud` should look like every other CLI's
        # "Invalid value for '--bitrate': ..." line.
        raise typer.BadParameter(str(exc), param_hint="--bitrate") from exc
    if fmt is OutputFormat.ALAC and bitrate != DEFAULT_LOSSY_BITRATE:
        console.print(f"[yellow]ignoring --bitrate {bitrate}: format alac is lossless")
    reports = pipeline.run(
        input_dir.resolve(),
        output_dir.resolve(),
        fmt=fmt,
        bitrate=bitrate_norm,
        enrich=enrich,
        dry_run=dry_run,
        verbose=verbose_local or bool(ctx.obj and ctx.obj.get("verbose")),
        allow_lossy_recompress=allow_lossy_recompress,
        workers=workers if workers > 0 else None,
        cover_max_edge=cover_max_edge,
        acoustid_key=acoustid_key.strip() or os.environ.get("MUSICKIT_ACOUSTID_KEY") or None,
        overwrite=overwrite,
        remove_source=remove_source,
        console=console,
    )
    failed = [r for r in reports if not r.ok]
    if failed:
        raise typer.Exit(code=1)
