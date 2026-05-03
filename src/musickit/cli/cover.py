"""`musickit cover IMAGE DIR` — embed an image into every audio file under DIR."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit import cover as cover_mod
from musickit.cli import app
from musickit.cover import DEFAULT_MAX_EDGE as DEFAULT_COVER_MAX_EDGE
from musickit.metadata import SUPPORTED_AUDIO_EXTS, embed_cover_only


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
