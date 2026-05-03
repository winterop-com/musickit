"""Typer CLI entry point — `musickit convert`, `musickit inspect`, ..."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit import cover as cover_mod
from musickit import library as library_mod
from musickit import pipeline
from musickit.convert import DEFAULT_LOSSY_BITRATE, OutputFormat, normalize_bitrate
from musickit.cover import DEFAULT_MAX_EDGE as DEFAULT_COVER_MAX_EDGE
from musickit.metadata import SUPPORTED_AUDIO_EXTS, TagOverrides, apply_tag_overrides, embed_cover_only, read_source

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Convert messy audio rips into a clean, tagged, organised library (FLAC/MP3/M4A → AAC m4a by default).",
)


@app.callback()
def _global_options(
    ctx: typer.Context,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Stream a log line per track instead of the progress bar. Works as a top-level flag (any subcommand).",
        ),
    ] = False,
) -> None:
    """Top-level musickit options that apply to every subcommand."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@app.command()
def convert(
    ctx: typer.Context,
    input_dir: Annotated[Path, typer.Argument(help="Root folder containing albums.")] = Path("./input"),
    output_dir: Annotated[Path, typer.Argument(help="Where to write `<Artist>/<Album> (Year)/` folders.")] = Path(
        "./output"
    ),
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


@app.command()
def cover(
    image: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Cover image (JPG/PNG).")],
    target_dir: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Directory whose audio files get this cover.")
    ],
    cover_max_edge: Annotated[
        int,
        typer.Option(
            "--cover-max-edge",
            min=128,
            help="Maximum cover dimension (px) on the long edge. Default 1000.",
        ),
    ] = DEFAULT_COVER_MAX_EDGE,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive/--no-recursive",
            help="Walk subdirectories. Default on — multi-disc albums get covered in one shot.",
        ),
    ] = True,
) -> None:
    """Embed IMAGE into every audio file under TARGET_DIR.

    The image is normalised (downscaled to fit the long-edge cap, JPEG-encoded
    for non-PNG sources) once and then written to every supported audio file.
    Other tags are preserved — only the cover is replaced.
    """
    console = Console()
    image_bytes = image.read_bytes()
    suffix = image.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in (".jpg", ".jpeg") else None
    if mime is None:
        raise typer.BadParameter(f"unsupported image extension: {image.suffix} (use .jpg / .jpeg / .png)")

    candidate = cover_mod.CoverCandidate(
        source=cover_mod.CoverSource.FOLDER,
        data=image_bytes,
        mime=mime,
        width=0,
        height=0,
        label=image.name,
    )
    width, height = cover_mod._measure(image_bytes)
    if width == 0:
        raise typer.BadParameter(f"could not decode image: {image}")
    candidate.width, candidate.height = width, height
    normalized = cover_mod.normalize(candidate, max_edge=cover_max_edge)

    iterator = target_dir.rglob("*") if recursive else target_dir.iterdir()
    audio_files = [p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS]
    if not audio_files:
        console.print(f"[yellow]no audio files found under {target_dir}[/yellow]")
        raise typer.Exit(code=1)

    console.print(
        f"[cyan]embedding[/cyan] {image.name} → {len(audio_files)} files "
        f"({normalized.width}×{normalized.height}, {len(normalized.data) // 1024} KB)"
    )
    failed: list[tuple[Path, str]] = []
    for path in sorted(audio_files):
        try:
            embed_cover_only(path, cover_bytes=normalized.data, cover_mime=normalized.mime)
        except Exception as exc:  # pragma: no cover — surface unexpected I/O / format errors
            failed.append((path, str(exc)))
            console.print(f"  [red]✗[/red] {path.name}: {exc}")
            continue
        console.print(f"  [green]✓[/green] {path.relative_to(target_dir)}")
    if failed:
        console.print(f"[red]{len(failed)} file(s) failed[/red]")
        raise typer.Exit(code=1)


@app.command()
def retag(
    target_dir: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Directory whose audio files get re-tagged.")
    ],
    album: Annotated[str | None, typer.Option("--album", help="Override album title.")] = None,
    artist: Annotated[str | None, typer.Option("--artist", help="Override per-track artist.")] = None,
    album_artist: Annotated[
        str | None,
        typer.Option(
            "--album-artist",
            help="Override album artist. Use `Various Artists` for compilations.",
        ),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", help="Override every track's title (rarely useful — applies to all files)."),
    ] = None,
    year: Annotated[str | None, typer.Option("--year", help="Override release year (4-digit).")] = None,
    genre: Annotated[str | None, typer.Option("--genre", help="Override genre.")] = None,
    track_total: Annotated[
        int | None, typer.Option("--track-total", help="Override total-tracks count (track number kept as-is).")
    ] = None,
    disc_total: Annotated[
        int | None, typer.Option("--disc-total", help="Override total-discs count (disc number kept as-is).")
    ] = None,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive/--no-recursive",
            help="Walk subdirectories. Default on — multi-disc albums get re-tagged in one shot.",
        ),
    ] = True,
    rename: Annotated[
        bool,
        typer.Option(
            "--rename",
            help="After retagging, rename TARGET_DIR to `YYYY - Album` based on the new tags.",
        ),
    ] = False,
) -> None:
    """Override tags on every audio file under TARGET_DIR.

    Only fields you explicitly pass are written; everything else is preserved
    (including covers, replaygain, MusicBrainz IDs). Useful when an album
    converted with the wrong name and you don't want to re-encode just to fix
    a tag.
    """
    overrides = TagOverrides(
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        year=year,
        genre=genre,
        track_total=track_total,
        disc_total=disc_total,
    )
    if overrides.is_empty():
        raise typer.BadParameter(
            "pass at least one of --album / --artist / --album-artist / --title / "
            "--year / --genre / --track-total / --disc-total"
        )

    console = Console()
    iterator = target_dir.rglob("*") if recursive else target_dir.iterdir()
    audio_files = sorted(p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTS)
    if not audio_files:
        console.print(f"[yellow]no audio files found under {target_dir}[/yellow]")
        raise typer.Exit(code=1)

    fields = ", ".join(f"{k}={v!r}" for k, v in overrides.model_dump().items() if v is not None)
    console.print(f"[cyan]retagging[/cyan] {len(audio_files)} files: {fields}")
    failed: list[tuple[Path, str]] = []
    for path in audio_files:
        try:
            apply_tag_overrides(path, overrides)
        except Exception as exc:  # pragma: no cover — surface unexpected I/O / format errors
            failed.append((path, str(exc)))
            console.print(f"  [red]✗[/red] {path.name}: {exc}")
            continue
        console.print(f"  [green]✓[/green] {path.relative_to(target_dir)}")
    if failed:
        console.print(f"[red]{len(failed)} file(s) failed[/red]")
        raise typer.Exit(code=1)

    if rename:
        # Re-read the first track to learn the post-update album/year, then
        # rebuild the dir name via the same `naming.album_folder` the convert
        # pipeline uses. Idempotent: if the dir is already named correctly,
        # no rename happens.
        from musickit import naming

        first = read_source(audio_files[0])
        if not first.album:
            console.print("[yellow]--rename: no album tag after retag, skipping[/yellow]")
            return
        new_name = naming.album_folder(first.album, first.date)
        new_dir = target_dir.parent / new_name
        if new_dir.resolve() == target_dir.resolve():
            return
        if new_dir.exists():
            console.print(f"[red]--rename: target {new_dir} already exists, skipping[/red]")
            raise typer.Exit(code=1)
        target_dir.rename(new_dir)
        console.print(f"[cyan]renamed[/cyan] {target_dir.name} → {new_dir.name}")


@app.command()
def library(
    ctx: typer.Context,
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root to scan."),
    ] = Path("./output"),
    audit_mode: Annotated[
        bool,
        typer.Option("--audit", help="Show the audit table (artist | album | year | tracks | cover | warnings)."),
    ] = False,
    issues_only: Annotated[
        bool,
        typer.Option("--issues-only", help="Only show albums with audit warnings (implies --audit)."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit the index as JSON instead of rendering."),
    ] = False,
    fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            help=(
                "Apply deterministic fixes to flagged albums: MB year backfill for missing years, "
                "rename dirs to match tags. Use `--dry-run` to preview without writing."
            ),
        ),
    ] = False,
    fix_dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="With `--fix`: print planned actions but don't write or rename."),
    ] = False,
    prefer_dirname: Annotated[
        bool,
        typer.Option(
            "--prefer-dirname",
            help=(
                "With `--fix`: when tag and path disagree, write the tag from the dir name "
                "(default is the opposite — rename the dir to match the tag)."
            ),
        ),
    ] = False,
) -> None:
    """Walk a converted-output directory and print an Artist→Album→Track index.

    Default render is a `rich.Tree`. `--audit` / `--issues-only` switch to a
    table that flags concrete cleanup actions (no cover, missing year, scene
    residue in names, track gaps, tag/path mismatches, and so on). `--fix`
    closes the loop on the deterministic warnings.
    """
    console = Console()
    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    # Audit modes need cover-pixel measurement so the low-res-cover rule can
    # fire. Otherwise stay in fast scan mode (no Pillow decode per cover).
    measure_pictures = audit_mode or issues_only or fix
    index = _scan_with_progress(
        console,
        target_dir.resolve(),
        verbose=verbose,
        measure_pictures=measure_pictures,
    )
    library_mod.audit(index)

    if fix:
        actions = library_mod.fix_index(
            index,
            dry_run=fix_dry_run,
            console=console,
            prefer_dirname=prefer_dirname,
        )
        prefix = "[yellow]would apply[/yellow]" if fix_dry_run else "[cyan]applied[/cyan]"
        console.print(f"{prefix} {len(actions)} fix(es)")
        return

    if json_out:
        console.print_json(index.model_dump_json())
        return

    if audit_mode or issues_only:
        _render_audit_table(console, index, issues_only=issues_only)
        return

    _render_tree(console, index)


def _scan_with_progress(
    console: Console,
    root: Path,
    *,
    verbose: bool,
    measure_pictures: bool = False,
) -> library_mod.LibraryIndex:
    """Wrap `library.scan` with a progress bar (or per-album lines if -v).

    Large libraries on slow drives (network, USB) can take seconds to minutes;
    we want feedback either way. Default is a transient rich.Progress spinner
    that reports `Scanning <album>  N/M`. Verbose prints one line per album so
    the output survives in scrollback for debugging.
    """
    from pathlib import PurePath

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

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
        task = progress.add_task("[cyan]Scanning library", total=None)

        def on_album(album_dir: Path, idx: int, total: int) -> None:
            if progress.tasks[task].total is None:
                progress.update(task, total=total)
            name = album_dir.name
            if len(name) > 40:
                name = name[:39] + "…"
            progress.update(task, advance=1, description=f"[cyan]Scanning[/] {name}")

        return library_mod.scan(root, on_album=on_album, measure_pictures=measure_pictures)


def _render_tree(console: Console, index: library_mod.LibraryIndex) -> None:
    from rich.tree import Tree

    tree = Tree(f"[bold]{index.root}[/bold]  ([dim]{len(index.albums)} albums[/dim])")
    by_artist: dict[str, list[library_mod.LibraryAlbum]] = {}
    for album in index.albums:
        by_artist.setdefault(album.artist_dir, []).append(album)
    for artist in sorted(by_artist, key=str.lower):
        artist_node = tree.add(f"[cyan]{artist}[/cyan]")
        for album in by_artist[artist]:
            warn = f" [yellow]⚠ {len(album.warnings)}[/yellow]" if album.warnings else ""
            cover = "" if album.has_cover else " [red](no cover)[/red]"
            artist_node.add(f"{album.album_dir}  [dim]({album.track_count} tracks)[/dim]{cover}{warn}")
    console.print(tree)


def _render_audit_table(
    console: Console,
    index: library_mod.LibraryIndex,
    *,
    issues_only: bool,
) -> None:
    from rich.table import Table

    rows = [a for a in index.albums if (not issues_only or a.warnings)]
    label = "flagged" if issues_only else "total"
    title = f"musickit library audit — {len(rows)} {label} of {len(index.albums)} albums"
    table = Table(title=title, show_lines=False)
    table.add_column("Artist", style="cyan")
    table.add_column("Album")
    table.add_column("Year")
    table.add_column("Tracks", justify="right")
    table.add_column("Cover")
    table.add_column("Warnings", style="yellow")

    for album in rows:
        cover_cell = (
            f"{album.cover_pixels // 1000}k px"
            if album.has_cover and album.cover_pixels
            else ("✓" if album.has_cover else "[red]✗[/red]")
        )
        year, _ = library_mod._split_dir_year(album.album_dir)
        warnings_cell = "\n".join(album.warnings) if album.warnings else "-"
        table.add_row(
            album.artist_dir,
            album.album_dir,
            year or album.tag_year or "-",
            str(album.track_count),
            cover_cell,
            warnings_cell,
        )
    console.print(table)


@app.command()
def tui(
    target_dir: Annotated[
        Path | None,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Library root to browse + play. Omit to launch in radio-only mode.",
        ),
    ] = None,
) -> None:
    """Browse and play the converted library in a Textual TUI.

    Layout: top status block (current track + time + state + volume), left
    library tree (artist → album), right playlist with a marker on the
    playing row, bottom keybinding hints. Decoding via PyAV (in-process,
    no external player). Audio output via sounddevice/PortAudio (bundled).

    When `TARGET_DIR` is omitted the TUI starts in radio-only mode — no
    library scan, the sidebar shows just the curated Radio entry.
    """
    from musickit.tui.app import MusickitApp

    MusickitApp(target_dir.resolve() if target_dir is not None else None).run()


if __name__ == "__main__":  # pragma: no cover
    app()
