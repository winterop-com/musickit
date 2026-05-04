"""`musickit library` — Artist→Album→Track index of the converted output, with audit + fix.

Also the canonical CLI for managing the persistent SQLite index DB at
`<root>/.musickit/index.db`: build / rebuild it (`--full-rescan`), inspect
it (`--index-status`), or wipe it (`--drop-index`).
"""

from __future__ import annotations

import shutil
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
    ],
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
    full_rescan: Annotated[
        bool,
        typer.Option(
            "--full-rescan",
            help=(
                "Rebuild the index DB from scratch, ignoring any cached rows. "
                "Without this flag, an existing index is loaded and only filesystem "
                "deltas (added / removed / tag-edited albums) are re-scanned."
            ),
        ),
    ] = False,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Skip the index DB entirely; in-memory scan only. Use for read-only mounts.",
        ),
    ] = False,
    drop_index: Annotated[
        bool,
        typer.Option(
            "--drop-index",
            help="Delete `<DIR>/.musickit/` (the persistent index DB) and exit.",
        ),
    ] = False,
    index_status: Annotated[
        bool,
        typer.Option(
            "--index-status",
            help="Print index DB metadata + counts and exit.",
        ),
    ] = False,
) -> None:
    """Walk a converted-output directory and print an Artist→Album→Track index.

    Default render is a `rich.Tree`. `--audit` / `--issues-only` switch to a
    table that flags concrete cleanup actions (no cover, missing year, scene
    residue in names, track gaps, tag/path mismatches, and so on). `--fix`
    closes the loop on the deterministic warnings.

    The persistent index lives at `<DIR>/.musickit/index.db`. Use
    `--index-status` to inspect it, `--full-rescan` to rebuild it, or
    `--drop-index` to delete it (it'll be recreated on the next scan).
    """
    console = Console()
    root = target_dir.resolve()

    if drop_index:
        _drop_index(console, root)
        return
    if index_status:
        _show_index_status(console, root)
        return

    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    # Audit modes need cover-pixel measurement so the low-res-cover rule can
    # fire. Otherwise stay in fast scan mode (no Pillow decode per cover).
    measure_pictures = audit_mode or issues_only or fix
    index = _scan_with_progress(
        console,
        root,
        verbose=verbose,
        measure_pictures=measure_pictures,
        use_cache=not no_cache,
        force=full_rescan,
    )

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
    use_cache: bool = True,
    force: bool = False,
) -> library_mod.LibraryIndex:
    """Thin wrapper that delegates to the shared scan-progress helper."""
    from musickit.cli._scan import scan_with_progress

    return scan_with_progress(
        console,
        root,
        verbose=verbose,
        measure_pictures=measure_pictures,
        use_cache=use_cache,
        force=force,
    )


def _drop_index(console: Console, root: Path) -> None:
    """Delete `<root>/.musickit/`. Idempotent."""
    index_dir = root / library_mod.INDEX_DIR_NAME
    if not index_dir.exists():
        console.print(f"[dim]no index at {index_dir} — nothing to drop[/dim]")
        return
    shutil.rmtree(index_dir)
    console.print(f"[green]removed[/green] {index_dir}")


def _show_index_status(console: Console, root: Path) -> None:
    """Print DB metadata + counts. Opens the DB read-only-ish."""
    db_file = library_mod.db_path(root)
    if not db_file.exists():
        console.print(f"[yellow]no index at {db_file}[/yellow]")
        console.print("[dim]run `musickit library DIR` (or `--full-rescan`) to build one[/dim]")
        return

    conn = library_mod.open_db(root)
    try:
        meta_rows = list(conn.execute("SELECT key, value FROM meta ORDER BY key"))
        album_count = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        track_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        warning_count = conn.execute("SELECT COUNT(*) FROM album_warnings").fetchone()[0]
        genre_count = conn.execute("SELECT COUNT(DISTINCT genre) FROM track_genres").fetchone()[0]
    finally:
        conn.close()

    db_size = db_file.stat().st_size

    from rich.table import Table

    table = Table(title=f"musickit library index — {db_file}", show_header=False)
    table.add_column(style="cyan")
    table.add_column()
    for key, value in meta_rows:
        table.add_row(key, value)
    table.add_row("albums", str(album_count))
    table.add_row("tracks", str(track_count))
    table.add_row("distinct genres", str(genre_count))
    table.add_row("audit warnings", str(warning_count))
    table.add_row("db size", _format_size(db_size))
    console.print(table)


def _format_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if f < 1024:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TiB"


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
