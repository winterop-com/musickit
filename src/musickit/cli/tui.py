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
            help="Library root to browse + play. Omit for radio-only or Subsonic-client mode.",
        ),
    ] = None,
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            help="Subsonic server URL — turns the TUI into a remote client. "
            "Falls back to ~/.config/musickit/state.json when omitted.",
        ),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Subsonic username. Falls back to state.json."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Subsonic password. Falls back to state.json."),
    ] = None,
) -> None:
    """Browse and play the converted library, internet radio, or any Subsonic server.

    Three modes:
      - `musickit tui DIR`                  — local library at DIR
      - `musickit tui`                      — radio-only (no scan)
      - `musickit tui --server URL ...`     — Subsonic client; works against
                                              any compatible server (musickit
                                              serve, Navidrome, real Subsonic).

    Server credentials persist to `~/.config/musickit/state.json` after a
    successful login, so subsequent runs can drop the flags and just say
    `musickit tui --server URL` (or even nothing — see below).

    With no arguments, the TUI prefers stored Subsonic creds over radio-only
    mode, so once you've logged in once on a machine, `musickit tui` resumes
    the remote library.
    """
    from musickit.tui.state import load_state, save_state

    saved = load_state()
    saved_subsonic = saved.get("subsonic") if isinstance(saved.get("subsonic"), dict) else {}
    assert isinstance(saved_subsonic, dict)  # narrows for type-checker

    final_url = server or _str_or_none(saved_subsonic.get("url"))
    final_user = user or _str_or_none(saved_subsonic.get("user"))
    final_password = password or _str_or_none(saved_subsonic.get("password"))

    use_subsonic = (server is not None) or (target_dir is None and final_url and final_user and final_password)

    if use_subsonic:
        if not (final_url and final_user and final_password):
            typer.echo(
                "error: Subsonic mode requires --server, --user, and --password "
                "(or stored credentials in ~/.config/musickit/state.json)",
                err=True,
            )
            raise typer.Exit(code=1)

        from musickit.tui.app import MusickitApp
        from musickit.tui.subsonic_client import SubsonicClient, SubsonicError

        client = SubsonicClient(final_url, final_user, final_password)
        try:
            client.ping()
        except SubsonicError as exc:
            typer.echo(f"error: subsonic ping failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        # Persist creds on successful login so the next launch can drop flags.
        new_state = dict(saved)
        new_state["subsonic"] = {"url": final_url, "user": final_user, "password": final_password}
        save_state(new_state)

        typer.echo(f"connected to {final_url} as {final_user}")
        MusickitApp(root=None, subsonic_client=client).run()
        return

    from musickit.tui.app import MusickitApp

    MusickitApp(target_dir.resolve() if target_dir is not None else None).run()


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
