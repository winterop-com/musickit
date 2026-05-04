"""`musickit library` — every operation that reads, mutates, or manages the converted library.

Subcommands:

- `tree DIR`            rich.Tree render (default for "what's in here")
- `audit DIR`           audit table with warnings
- `fix DIR`             apply the deterministic fixes flagged by audit
- `cover DIR IMAGE`     embed an image into every audio file
- `cover-pick DIR`      semi-automated cover sourcing via musichoarders
- `retag DIR`           in-place tag overrides
- `index status|drop|rebuild DIR`   manage the `<DIR>/.musickit/index.db` cache

The single-command form (`musickit library DIR --audit / --fix / --cover-pick / ...`) is
gone; pick a subcommand. The persistent index DB lives at `<DIR>/.musickit/index.db`
and is shared by `tui` and `serve`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from musickit import library as library_mod
from musickit.cli import app

library_app = typer.Typer(
    no_args_is_help=True,
    help="Read, audit, fix, retag, cover, and manage the converted library.",
)
app.add_typer(library_app, name="library")


# Shared option types — Annotated aliases reduce repetition across subcommands.
_NoCacheOpt = Annotated[
    bool,
    typer.Option(
        "--no-cache",
        help="Skip the index DB entirely; in-memory scan only. Use for read-only mounts.",
    ),
]
_FullRescanOpt = Annotated[
    bool,
    typer.Option(
        "--full-rescan",
        help="Rebuild the index DB from scratch, ignoring any cached rows.",
    ),
]


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


@library_app.command("tree")
def library_tree(
    ctx: typer.Context,
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit the index as JSON instead of rendering."),
    ] = False,
    no_cache: _NoCacheOpt = False,
    full_rescan: _FullRescanOpt = False,
) -> None:
    """Render an Artist→Album→Track tree of the converted library."""
    console = Console()
    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    index = _scan_with_progress(
        console,
        target_dir.resolve(),
        verbose=verbose,
        measure_pictures=False,
        use_cache=not no_cache,
        force=full_rescan,
    )
    if json_out:
        console.print_json(index.model_dump_json())
        return
    _render_tree(console, index)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@library_app.command("audit")
def library_audit(
    ctx: typer.Context,
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    issues_only: Annotated[
        bool,
        typer.Option("--issues-only", help="Only show albums with audit warnings."),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit the audited index as JSON."),
    ] = False,
    no_cache: _NoCacheOpt = False,
    full_rescan: _FullRescanOpt = False,
) -> None:
    """Show the audit table — flagged cleanup actions per album."""
    console = Console()
    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    index = _scan_with_progress(
        console,
        target_dir.resolve(),
        verbose=verbose,
        # Audit needs cover-pixel measurement so the low-res-cover rule can fire.
        measure_pictures=True,
        use_cache=not no_cache,
        force=full_rescan,
    )
    if json_out:
        console.print_json(index.model_dump_json())
        return
    _render_audit_table(console, index, issues_only=issues_only)


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


@library_app.command("fix")
def library_fix(
    ctx: typer.Context,
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print planned actions but don't write or rename."),
    ] = False,
    prefer_dirname: Annotated[
        bool,
        typer.Option(
            "--prefer-dirname",
            help=(
                "When tag and dir disagree, write the tag from the dir name "
                "(default is the opposite — rename the dir to match the tag)."
            ),
        ),
    ] = False,
    no_cache: _NoCacheOpt = False,
    full_rescan: _FullRescanOpt = False,
) -> None:
    """Apply deterministic fixes to flagged albums (MB year, tag/dir rename)."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    console = Console()
    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    index = _scan_with_progress(
        console,
        target_dir.resolve(),
        verbose=verbose,
        measure_pictures=True,
        use_cache=not no_cache,
        force=full_rescan,
    )

    # Progress bar over the flagged-album subset. MB year lookups are slow
    # (one HTTP call per album), so silence here looks like a hang on a
    # 1k-album library; the per-album spinner makes that wait visible.
    flagged_count = sum(1 for a in index.albums if a.warnings)
    if flagged_count == 0:
        console.print("[green]nothing to fix[/green] — every album passes audit")
        return

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
        task = progress.add_task("[cyan]Fixing", total=flagged_count)

        def on_album(album: library_mod.LibraryAlbum, idx: int, total: int) -> None:
            del idx, total
            label = f"{album.artist_dir} / {album.album_dir}"
            if len(label) > 60:
                label = label[:59] + "…"
            progress.update(task, advance=1, description=f"[cyan]Fixing[/] [dim]·[/] {label}")

        actions = library_mod.fix_index(
            index,
            dry_run=dry_run,
            console=console,
            prefer_dirname=prefer_dirname,
            on_album=on_album,
        )

    prefix = "[yellow]would apply[/yellow]" if dry_run else "[cyan]applied[/cyan]"
    console.print(f"{prefix} {len(actions)} fix(es)")


# ---------------------------------------------------------------------------
# index management
# ---------------------------------------------------------------------------


index_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the persistent SQLite index DB at `<DIR>/.musickit/index.db`.",
)
library_app.add_typer(index_app, name="index")


@index_app.command("status")
def index_status(
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
) -> None:
    """Print index DB metadata + counts."""
    console = Console()
    root = target_dir.resolve()
    db_file = library_mod.db_path(root)
    if not db_file.exists():
        console.print(f"[yellow]no index at {db_file}[/yellow]")
        console.print(f"[dim]run `musickit library tree {target_dir}` to build one[/dim]")
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


@index_app.command("drop")
def index_drop(
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
) -> None:
    """Delete `<DIR>/.musickit/` (the persistent index DB). Idempotent."""
    console = Console()
    root = target_dir.resolve()
    index_dir = root / library_mod.INDEX_DIR_NAME
    if not index_dir.exists():
        console.print(f"[dim]no index at {index_dir} — nothing to drop[/dim]")
        return
    shutil.rmtree(index_dir)
    console.print(f"[green]removed[/green] {index_dir}")


@index_app.command("rebuild")
def index_rebuild(
    ctx: typer.Context,
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    no_cache: _NoCacheOpt = False,
) -> None:
    """Rebuild the index DB from scratch (ignore cached rows). Equivalent to `tree --full-rescan`, no render."""
    console = Console()
    verbose = bool(ctx.obj and ctx.obj.get("verbose"))
    root = target_dir.resolve()
    index = _scan_with_progress(
        console,
        root,
        verbose=verbose,
        measure_pictures=False,
        use_cache=not no_cache,
        force=True,
    )
    console.print(
        f"[green]rebuilt[/green] {len(index.albums)} albums, {sum(a.track_count for a in index.albums)} tracks"
    )
    if not no_cache:
        console.print(f"[dim]→ {library_mod.db_path(root)}[/dim]")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


def _format_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if f < 1024:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TiB"
