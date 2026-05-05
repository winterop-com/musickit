"""Typer CLI — `musickit convert`, `musickit inspect`, ... .

The `app` Typer instance lives in this module; per-command modules import
it to register their handlers. Importing them at the bottom of the file
triggers `@app.command()` registration for every subcommand.
"""

from __future__ import annotations

from typing import Annotated

import typer

from musickit import __version__

# Multi-line help shown when the user runs bare `musickit` (with
# `no_args_is_help=True`). Typer renders this above the Usage / Options
# / Commands tables. We pack in version + doc / repo links + the four
# most common starting commands so a new user sees what's available
# without hunting through `--help` on every subcommand.
_APP_HELP = (
    f"Self-hosted music toolkit (v{__version__}) — convert audio rips into a clean tagged "
    "library, browse and play via a Textual TUI, and stream over Tailscale via a "
    "Subsonic-compatible HTTP server."
    """

[bold]Common starts[/]

  [cyan]musickit convert ./input ./output[/]      Convert a rips dir into a clean library
  [cyan]musickit library audit ./output[/]        Audit the converted library for issues
  [cyan]musickit tui ./output[/]                  Browse + play the library locally
  [cyan]musickit serve ./output[/]                Subsonic server for iOS / Android clients

Pass [cyan]--help[/] after any subcommand for its options.

[bold]Links[/]

  Docs:  https://winterop-com.github.io/musickit/
  PyPI:  https://pypi.org/project/musickit/
  Repo:  https://github.com/winterop-com/musickit
"""
)


app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    help=_APP_HELP,
)


def _print_version(value: bool) -> None:
    """`--version` callback — print version and exit before any subcommand runs."""
    if not value:
        return
    typer.echo(f"musickit {__version__}")
    raise typer.Exit()


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
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the musickit version and exit.",
        ),
    ] = False,
) -> None:
    """Top-level musickit options that apply to every subcommand."""
    del version  # handled by the eager callback before this body runs
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# Import command modules so their `@app.command()` / `@library_app.command()`
# decorators register. `library` MUST come before cover/cover_pick/retag —
# those modules register on `library.library_app` and need it defined first.
# These are side-effect imports — the `_` assignment shuts up pyright's
# reportUnusedImport without us needing per-line ignores.
from musickit.cli import convert as _convert_cmd  # noqa: E402, I001
from musickit.cli import cover as _cover_cmd  # noqa: E402
from musickit.cli import cover_pick as _cover_pick_cmd  # noqa: E402
from musickit.cli import inspect as _inspect_cmd  # noqa: E402
from musickit.cli import library as _library_cmd  # noqa: E402
from musickit.cli import playlist as _playlist_cmd  # noqa: E402
from musickit.cli import retag as _retag_cmd  # noqa: E402
from musickit.cli import serve as _serve_cmd  # noqa: E402
from musickit.cli import tui as _tui_cmd  # noqa: E402

_ = (
    _convert_cmd,
    _cover_cmd,
    _cover_pick_cmd,
    _inspect_cmd,
    _library_cmd,
    _playlist_cmd,
    _retag_cmd,
    _serve_cmd,
    _tui_cmd,
)
