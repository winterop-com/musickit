"""`musickit tui` — launch the Textual TUI."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from musickit.cli import app


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
