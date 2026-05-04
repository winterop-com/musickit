"""Typer CLI — `musickit convert`, `musickit inspect`, ... .

The `app` Typer instance lives in this module; per-command modules import
it to register their handlers. Importing them at the bottom of the file
triggers `@app.command()` registration for every subcommand.
"""

from __future__ import annotations

from typing import Annotated

import typer

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


# Import command modules so their `@app.command()` decorators register.
# Order doesn't matter for Typer; alphabetical for readability.
# These are side-effect imports — the `_` assignment shuts up pyright's
# reportUnusedImport without us needing per-line ignores.
from musickit.cli import convert as _convert_cmd  # noqa: E402
from musickit.cli import cover as _cover_cmd  # noqa: E402
from musickit.cli import cover_pick as _cover_pick_cmd  # noqa: E402
from musickit.cli import inspect as _inspect_cmd  # noqa: E402
from musickit.cli import library as _library_cmd  # noqa: E402
from musickit.cli import retag as _retag_cmd  # noqa: E402
from musickit.cli import serve as _serve_cmd  # noqa: E402
from musickit.cli import tui as _tui_cmd  # noqa: E402

_ = (
    _convert_cmd,
    _cover_cmd,
    _cover_pick_cmd,
    _inspect_cmd,
    _library_cmd,
    _retag_cmd,
    _serve_cmd,
    _tui_cmd,
)
