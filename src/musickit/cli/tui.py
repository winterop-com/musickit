"""`musickit tui` — launch the Textual TUI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from musickit.cli import app

if TYPE_CHECKING:
    from musickit.tui.airplay import AirPlayController


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
    subsonic: Annotated[
        str | None,
        typer.Option(
            "--subsonic",
            help=(
                "Subsonic server URL — turns the TUI into a remote client. "
                "Pass `--subsonic` with no value to use the saved server (--save-subsonic)."
            ),
        ),
    ] = None,
    use_saved_subsonic: Annotated[
        bool,
        typer.Option(
            "--saved-subsonic",
            help="Reconnect to the saved Subsonic server (no --subsonic / --user / --password needed).",
        ),
    ] = False,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Subsonic username."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Subsonic password."),
    ] = None,
    save_subsonic_creds: Annotated[
        bool,
        typer.Option(
            "--save-subsonic",
            help=(
                "Persist (host, user, token, salt) to ~/.config/musickit/state.toml on successful "
                "connect. Future launches use --saved-subsonic. Token-derived; raw password is NOT stored."
            ),
        ),
    ] = False,
    forget_subsonic: Annotated[
        bool,
        typer.Option(
            "--forget-subsonic",
            help="Remove the saved Subsonic auth block from state.toml and exit.",
        ),
    ] = False,
    discover: Annotated[
        bool,
        typer.Option(
            "--discover",
            help="Browse the LAN for Subsonic servers and AirPlay devices, print, and exit.",
        ),
    ] = False,
    airplay: Annotated[
        str | None,
        typer.Option(
            "--airplay",
            help="Route playback to this AirPlay device (substring match against name or address).",
        ),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Skip the persistent index DB at `<DIR>/.musickit/index.db`; in-memory scan only.",
        ),
    ] = False,
    full_rescan: Annotated[
        bool,
        typer.Option(
            "--full-rescan",
            help="Rebuild the index DB from scratch on launch (ignores any cached rows).",
        ),
    ] = False,
) -> None:
    """Browse and play the converted library, internet radio, or any Subsonic server.

    Three modes:
      - `musickit tui DIR`                       — local library at DIR
      - `musickit tui`                           — radio-only (no scan)
      - `musickit tui --subsonic URL ...`        — Subsonic client; works against
                                                   any compatible server (musickit
                                                   serve, Navidrome, real Subsonic).

    Subsonic credentials are NEVER persisted — pass --subsonic / --user /
    --password explicitly each session if you want client mode. Without
    those flags the TUI starts in local-library mode (with `DIR`) or
    radio-only mode (without).
    """
    from musickit.tui.state import clear_subsonic, load_state, load_subsonic, save_subsonic

    if forget_subsonic:
        if clear_subsonic():
            typer.echo("forgot saved Subsonic credentials.")
        else:
            typer.echo("no saved Subsonic credentials to forget.")
        return

    if discover:
        _run_discover_and_exit()  # raises Exit
        return

    saved = load_state()

    from musickit.tui.app import MusickitApp
    from musickit.tui.subsonic_client import SubsonicClient, SubsonicError

    connected_client = None
    if use_saved_subsonic:
        block = load_subsonic()
        if block is None:
            typer.echo(
                "error: --saved-subsonic but no saved credentials. "
                "Connect once with --subsonic / --user / --password / --save-subsonic first.",
                err=True,
            )
            raise typer.Exit(code=1)
        client = SubsonicClient(
            block["host"],
            block["user"],
            token=block["token"],
            salt=block["salt"],
            timeout=15.0,
        )
        try:
            client.ping()
        except SubsonicError as exc:
            typer.echo(f"error: subsonic ping with saved creds failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"connected to {block['host']} as {block['user']} (saved creds)")
        connected_client = client
    elif subsonic is not None:
        if not (user and password):
            typer.echo(
                "error: --subsonic requires --user and --password (or use --saved-subsonic)",
                err=True,
            )
            raise typer.Exit(code=1)

        # Derive a token + salt up front. Even when not saving, the
        # client uses token-auth on the wire — keeps password out of
        # query strings end-to-end.
        token, salt = SubsonicClient.derive_token(password)
        client = SubsonicClient(subsonic, user, token=token, salt=salt, timeout=15.0)
        try:
            client.ping()
        except SubsonicError as exc:
            typer.echo(f"error: subsonic ping failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"connected to {subsonic} as {user}")
        connected_client = client

        if save_subsonic_creds:
            save_subsonic(host=subsonic.rstrip("/"), user=user, token=token, salt=salt)
            typer.echo("saved Subsonic credentials to ~/.config/musickit/state.toml")

    airplay_controller = None
    if airplay:
        airplay_controller = _connect_airplay_or_exit(airplay)
    else:
        saved_airplay = saved.get("airplay")
        if isinstance(saved_airplay, dict):
            # Auto-resume previously selected AirPlay device. Best-effort: skip
            # silently if the device isn't on the LAN right now (don't make the
            # user wait through a 5s timeout for a HomePod that's powered off).
            airplay_controller = _try_resume_airplay(saved_airplay)

    if connected_client is not None:
        MusickitApp(root=None, subsonic_client=connected_client, airplay=airplay_controller).run()
        return

    # Last fallback: if there's no DIR and no live Subsonic connection, briefly
    # browse mDNS for any musickit servers on the LAN and surface them as a
    # hint before dropping into radio-only mode.
    if target_dir is None:
        _print_lan_hint_if_any()

    MusickitApp(
        target_dir.resolve() if target_dir is not None else None,
        airplay=airplay_controller,
        use_cache=not no_cache,
        force_rescan=full_rescan,
    ).run()


def _run_discover_and_exit() -> None:
    """Browse the LAN for Subsonic servers + AirPlay devices, print, exit."""
    from musickit.tui.airplay import AirPlayController
    from musickit.tui.discovery import browse_subsonic_servers

    typer.echo("Browsing for Subsonic servers (mDNS)…")
    servers = browse_subsonic_servers(timeout=2.0)
    if servers:
        for s in servers:
            marker = "musickit" if s.is_musickit else "other"
            typer.echo(f"  • [{marker}] {s.name}  →  {s.url}")
    else:
        typer.echo("  (none found)")

    typer.echo("\nScanning for AirPlay devices…")
    controller = AirPlayController()
    try:
        devices = controller.discover()
    finally:
        controller.disconnect()
    if devices:
        for d in devices:
            typer.echo(f"  • {d.display_label}")
    else:
        typer.echo("  (none found)")

    if servers:
        typer.echo("\nConnect to a Subsonic server with:")
        typer.echo(f"  musickit tui --subsonic {servers[0].url} --user <U> --password <P>")
    if devices:
        typer.echo("\nUse an AirPlay device with:")
        typer.echo(f"  musickit tui --airplay '{devices[0].name}' [...]")
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
    typer.echo(f"  Connect with: musickit tui --subsonic {musickit_servers[0].url} --user <U> --password <P>\n")


def _connect_airplay_or_exit(name_substring: str) -> AirPlayController:
    """Discover devices, pick by substring, connect. Exits non-zero on no match / failure."""
    from musickit.tui.airplay import AirPlayController

    typer.echo(f"Looking for AirPlay device matching '{name_substring}'…")
    controller = AirPlayController()
    try:
        devices = controller.discover()
    except Exception as exc:
        typer.echo(f"error: AirPlay discovery failed: {exc}", err=True)
        controller.disconnect()
        raise typer.Exit(code=1) from exc

    needle = name_substring.casefold()
    matches = [d for d in devices if needle in d.name.casefold() or needle in d.address.casefold()]
    if not matches:
        typer.echo(
            f"error: no AirPlay device matched '{name_substring}'. Run with --discover to list available devices.",
            err=True,
        )
        controller.disconnect()
        raise typer.Exit(code=1)
    if len(matches) > 1:
        typer.echo(
            f"error: '{name_substring}' matched {len(matches)} devices: "
            f"{', '.join(d.name for d in matches)}. Be more specific.",
            err=True,
        )
        controller.disconnect()
        raise typer.Exit(code=1)

    chosen = matches[0]
    try:
        controller.connect(chosen)
    except Exception as exc:
        typer.echo(f"error: failed to connect to {chosen.display_label}: {exc}", err=True)
        controller.disconnect()
        raise typer.Exit(code=1) from exc
    typer.echo(f"AirPlay → {chosen.display_label}")
    return controller


def _try_resume_airplay(saved: dict[str, object]) -> AirPlayController | None:
    """Re-connect to a previously selected AirPlay device. Best-effort, silent on failure.

    Looks up the device by identifier first (most stable across DHCP / SSID
    changes), then by name. If neither matches a currently-discoverable
    device, returns None and the TUI starts in local-audio mode.
    """
    from musickit.tui.airplay import AirPlayController

    saved_id = _str_or_none(saved.get("identifier"))
    saved_name = _str_or_none(saved.get("name"))
    if not saved_id and not saved_name:
        return None
    controller = AirPlayController()
    try:
        devices = controller.discover(timeout=2.0)
    except Exception:
        controller.disconnect()
        return None
    match = None
    if saved_id:
        match = next((d for d in devices if d.identifier == saved_id), None)
    if match is None and saved_name:
        match = next((d for d in devices if d.name == saved_name), None)
    if match is None:
        controller.disconnect()
        return None
    try:
        controller.connect(match)
    except Exception:
        controller.disconnect()
        return None
    typer.echo(f"AirPlay → {match.display_label} (resumed from last session)")
    return controller


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
