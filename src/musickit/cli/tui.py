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
    discover: Annotated[
        bool,
        typer.Option("--discover", help="Browse the LAN for musickit / Subsonic servers, print, and exit."),
    ] = False,
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

    if discover:
        _run_discover_and_exit()  # raises Exit
        return

    saved = load_state()
    saved_subsonic = saved.get("subsonic") if isinstance(saved.get("subsonic"), dict) else {}
    assert isinstance(saved_subsonic, dict)  # narrows for type-checker

    final_url = server or _str_or_none(saved_subsonic.get("url"))
    final_user = user or _str_or_none(saved_subsonic.get("user"))
    final_password = password or _str_or_none(saved_subsonic.get("password"))

    explicit_server = server is not None
    use_subsonic = explicit_server or (target_dir is None and final_url and final_user and final_password)

    from musickit.tui.app import MusickitApp

    connected_client = None
    if use_subsonic:
        if not (final_url and final_user and final_password):
            typer.echo(
                "error: Subsonic mode requires --server, --user, and --password "
                "(or stored credentials in ~/.config/musickit/state.json)",
                err=True,
            )
            raise typer.Exit(code=1)

        from musickit.tui.subsonic_client import SubsonicClient, SubsonicError

        client = SubsonicClient(final_url, final_user, final_password)
        try:
            client.ping()
        except SubsonicError as exc:
            if explicit_server:
                # Explicit --server: hard fail. The user asked for this server.
                typer.echo(f"error: subsonic ping failed: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            # Auto-resumed from state.json — server's offline. Radio is a
            # first-class mode, not a fallback to apologise for; just close
            # the client and fall through silently.
            client.close()
        else:
            # Persist creds on successful login so the next launch can drop flags.
            new_state = dict(saved)
            new_state["subsonic"] = {"url": final_url, "user": final_user, "password": final_password}
            save_state(new_state)
            typer.echo(f"connected to {final_url} as {final_user}")
            connected_client = client

    if connected_client is not None:
        MusickitApp(root=None, subsonic_client=connected_client).run()
        return

    # Last fallback: if there's no DIR and no live Subsonic connection, briefly
    # browse mDNS for any musickit servers on the LAN and surface them as a
    # hint before dropping into radio-only mode.
    if target_dir is None:
        _print_lan_hint_if_any()

    MusickitApp(target_dir.resolve() if target_dir is not None else None).run()


def _run_discover_and_exit() -> None:
    """Browse the LAN for Subsonic servers, print, exit."""
    from musickit.tui.discovery import browse_subsonic_servers

    typer.echo("Browsing for Subsonic servers (mDNS)…")
    servers = browse_subsonic_servers(timeout=2.0)
    if not servers:
        typer.echo("  (none found — make sure `musickit serve` is running on the LAN)")
        raise typer.Exit(0)
    for s in servers:
        marker = "musickit" if s.is_musickit else "other"
        typer.echo(f"  • [{marker}] {s.name}  →  {s.url}")
    typer.echo("\nConnect with:")
    typer.echo(f"  musickit tui --server {servers[0].url} --user <U> --password <P>")
    raise typer.Exit(0)


def _print_lan_hint_if_any() -> None:
    """Quick mDNS browse to nudge the user toward a server they didn't know about."""
    from musickit.tui.discovery import browse_subsonic_servers

    try:
        servers = browse_subsonic_servers(timeout=1.0)
    except Exception:  # pragma: no cover — discovery is best-effort
        return
    musickit_servers = [s for s in servers if s.is_musickit]
    if not musickit_servers:
        return
    typer.echo(f"Found {len(musickit_servers)} musickit server(s) on the LAN:")
    for s in musickit_servers[:3]:
        typer.echo(f"  • {s.url}")
    typer.echo(f"  Connect with: musickit tui --server {musickit_servers[0].url} --user <U> --password <P>\n")


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
