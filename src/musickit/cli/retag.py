"""`musickit library retag DIR` — override tags on every audio file under DIR in-place."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit.cli.library import library_app
from musickit.metadata import SUPPORTED_AUDIO_EXTS, TagOverrides, apply_tag_overrides, read_source


@library_app.command("retag")
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
