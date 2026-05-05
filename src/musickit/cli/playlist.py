"""`musickit playlist` — generate, list, and inspect auto-generated playlists.

Subcommands:

  gen ROOT --seed PATH        Generate a mix anchored to a seed track.
  list ROOT                   List the .m3u8 files under <ROOT>/.musickit/playlists/.
  show ROOT NAME              Print the contents of a saved playlist.

Phase 1 — tag-based similarity, no audio fingerprinting, no play history.
The smart-playlist DSL (saved queries that re-resolve at read time)
follows in a later pass once we have a play_history table.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from musickit import library as library_mod
from musickit import playlist as playlist_mod
from musickit.cli import app
from musickit.playlist.io import read_m3u8

playlist_app = typer.Typer(
    no_args_is_help=True,
    help="Generate and manage auto-generated playlists (.m3u8 files).",
)
app.add_typer(playlist_app, name="playlist")


_PLAYLISTS_SUBDIR = "playlists"


def _playlists_dir(root: Path) -> Path:
    """Return `<root>/.musickit/playlists/` (the canonical output location)."""
    return root / library_mod.INDEX_DIR_NAME / _PLAYLISTS_SUBDIR


def _slug(name: str) -> str:
    """Make a filename-safe slug from a playlist name."""
    cleaned = re.sub(r"[^\w\s-]", "", name).strip()
    cleaned = re.sub(r"[\s_-]+", "-", cleaned)
    return cleaned.lower() or "mix"


@playlist_app.command("gen")
def cmd_gen(
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    seed: Annotated[
        Path,
        typer.Option(
            "--seed",
            help="Path to the seed track. May be absolute or a bare filename present in the library.",
        ),
    ],
    minutes: Annotated[
        float,
        typer.Option("--minutes", help="Target playlist length in minutes."),
    ] = 60.0,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Output .m3u8 path. Defaults to <ROOT>/.musickit/playlists/<slug>.m3u8.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Playlist display name. Default: 'Mix - <artist> - <title>' from the seed.",
        ),
    ] = None,
    random_seed: Annotated[
        int | None,
        typer.Option(
            "--random-seed",
            help="Seed the tie-breaker RNG for reproducible output. Off by default.",
        ),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Skip the SQLite index DB; in-memory scan only."),
    ] = False,
    full_rescan: Annotated[
        bool,
        typer.Option("--full-rescan", help="Rebuild the index DB from scratch before generating."),
    ] = False,
) -> None:
    """Generate a playlist anchored to `--seed`."""
    console = Console()
    root = target_dir.resolve()
    index = library_mod.load_or_scan(root, use_cache=not no_cache, force=full_rescan)

    # The seed argument may be absolute, a relative path under the library
    # root, or just a filename. The builder accepts a string and matches
    # by absolute path or basename — pass `seed` through as a string.
    seed_arg: str = str(seed.resolve()) if seed.exists() else str(seed)

    try:
        result = playlist_mod.generate(
            index,
            seed_arg,
            target_minutes=minutes,
            name=name,
            random_seed=random_seed,
        )
    except ValueError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    out_path = out if out is not None else _playlists_dir(root) / f"{_slug(result.name)}.m3u8"
    written = playlist_mod.write_m3u8(result, out_path)

    actual_min = result.actual_seconds / 60.0
    console.print(
        f"[green]Generated[/] [bold]{result.name}[/] "
        f"([cyan]{len(result.tracks)}[/] tracks, "
        f"[cyan]{actual_min:.1f} min[/] / target {minutes:.0f} min)"
    )
    console.print(f"[dim]→ {written}[/]")


@playlist_app.command("list")
def cmd_list(
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
) -> None:
    """List the playlists saved under `<ROOT>/.musickit/playlists/`."""
    console = Console()
    pdir = _playlists_dir(target_dir.resolve())
    if not pdir.exists():
        console.print(f"[dim]No playlists yet — none under {pdir}[/]")
        return

    files = sorted(pdir.glob("*.m3u8"))
    if not files:
        console.print(f"[dim]No playlists yet — directory is empty: {pdir}[/]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Tracks", justify="right")
    table.add_column("Path", style="dim")
    for f in files:
        try:
            n = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
        except OSError:
            n = 0
        table.add_row(f.stem, str(n), str(f))
    console.print(table)


@playlist_app.command("show")
def cmd_show(
    target_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Library root."),
    ],
    name: Annotated[
        str,
        typer.Argument(help="Playlist file stem (without `.m3u8`) or a relative path."),
    ],
) -> None:
    """Print the tracks referenced by a saved playlist."""
    console = Console()
    pdir = _playlists_dir(target_dir.resolve())
    candidate = pdir / f"{name}.m3u8"
    if not candidate.exists():
        # Fall back to treating `name` as a literal path.
        candidate = Path(name)
        if not candidate.exists():
            typer.secho(f"Error: playlist not found: {name}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

    paths = read_m3u8(candidate)
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Path")
    table.add_column("Exists", justify="center")
    for i, p in enumerate(paths, start=1):
        marker = "[green]y[/]" if p.exists() else "[red]n[/]"
        table.add_row(str(i), str(p), marker)
    console.print(f"[bold]{candidate.stem}[/]  [dim]({len(paths)} tracks)[/]")
    console.print(table)
