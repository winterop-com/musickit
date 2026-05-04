"""`musickit library` — Artist→Album→Track index of the converted output, with audit + fix."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit import library as library_mod
from musickit.cli import app


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
    """Thin wrapper that delegates to the shared scan-progress helper."""
    from musickit.cli._scan import scan_with_progress

    return scan_with_progress(
        console,
        root,
        verbose=verbose,
        measure_pictures=measure_pictures,
    )


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
