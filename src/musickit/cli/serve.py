"""`musickit serve` — launch the Subsonic-compatible HTTP server."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from musickit.cli import app


@app.command()
def serve(
    target_dir: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Library root to expose.",
        ),
    ],
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Interface to bind. Default 0.0.0.0 covers LAN + Tailscale; "
            "use 127.0.0.1 to restrict to this machine.",
        ),
    ] = "0.0.0.0",  # noqa: S104 — LAN+Tailscale binding is the whole point
    port: Annotated[
        int,
        typer.Option("--port", help="Port. Defaults to 4533 (Navidrome's, which clients pre-fill)."),
    ] = 4533,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Username override. Falls back to ~/.config/musickit/serve.toml."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Password override. Falls back to ~/.config/musickit/serve.toml."),
    ] = None,
    no_mdns: Annotated[
        bool,
        typer.Option("--no-mdns", help="Skip mDNS / Bonjour advertisement."),
    ] = False,
    no_watch: Annotated[
        bool,
        typer.Option("--no-watch", help="Skip the filesystem watcher (auto-rescan on library changes)."),
    ] = False,
) -> None:
    """Start a Subsonic-compatible server for the converted library.

    Compatible with any Subsonic client (Symfonium, play:Sub, Feishin,
    DSub, Supersonic, etc.). The default `--host 0.0.0.0` binding makes
    the server reachable over Tailscale as well as the LAN.
    """
    from musickit.serve import create_app, resolve_credentials

    cfg, used_defaults = resolve_credentials(cli_user=user, cli_password=password)
    if used_defaults:
        typer.secho(
            "WARNING: using default credentials admin/admin — pass --user/--password "
            "or write `~/.config/musickit/serve.toml` for anything beyond a private LAN.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    fastapi_app = create_app(root=target_dir.resolve(), cfg=cfg)
    _print_startup_banner(host=host, port=port, root=target_dir.resolve())

    # Block on the initial scan so the first client request hits a populated
    # cache. Show a transient progress bar — large libraries on slow drives
    # take many seconds to walk, and silent stalls feel like a hang.
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    console = Console()
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
        task = progress.add_task("[cyan]Scanning library", total=None)

        def on_album(album_dir: Path, idx: int, total: int) -> None:
            if progress.tasks[task].total is None:
                progress.update(task, total=total)
            name = album_dir.name
            if len(name) > 40:
                name = name[:39] + "…"
            progress.update(task, advance=1, description=f"[cyan]Scanning[/] [dim]·[/] {name}")

        fastapi_app.state.cache.rebuild(on_album=on_album)
    cache = fastapi_app.state.cache
    typer.echo(f"  {cache.artist_count} artists, {cache.album_count} albums, {cache.track_count} tracks\n")

    mdns_handle = None
    if not no_mdns:
        from musickit.serve.discovery import register_service

        mdns_handle = register_service(port=port)
        if mdns_handle is not None:
            _, info = mdns_handle
            typer.echo(f"  mDNS: advertising as {info.name.rstrip('.')}")

    watcher = None
    if not no_watch:
        from musickit.serve.watcher import LibraryWatcher

        watcher = LibraryWatcher(fastapi_app.state.cache)
        watcher.start()
        typer.echo(f"  watching {target_dir.resolve()} for changes (auto-rescan on add/remove/rename)")

    import uvicorn

    try:
        uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
    finally:
        if watcher is not None:
            watcher.stop()
        if mdns_handle is not None:
            from musickit.serve.discovery import unregister_service

            zc, info = mdns_handle
            unregister_service(zc, info)


def _print_startup_banner(*, host: str, port: int, root: Path) -> None:
    """Show LAN + Tailscale URLs on startup so the user can copy/paste into a client."""
    typer.echo(f"musickit serve — Subsonic API for {root}")
    typer.echo(f"  bind: {host}:{port}")
    if host in ("0.0.0.0", "::"):
        lan = _local_lan_ip()
        if lan:
            typer.echo(f"  LAN:  http://{lan}:{port}")
        ts = _tailscale_hostname()
        if ts:
            typer.echo(f"  Tailscale: http://{ts}:{port}")
    typer.echo("")


def _local_lan_ip() -> str | None:
    """Best-effort: ask the OS which interface it'd use to reach a public IP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return str(ip) if ip else None


def _tailscale_hostname() -> str | None:
    """Return the tailnet hostname (MagicDNS) if `tailscale` is installed and up."""
    if shutil.which("tailscale") is None:
        return None
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    import json

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    self_node = data.get("Self", {})
    dns_name = self_node.get("DNSName", "")
    if isinstance(dns_name, str) and dns_name:
        return dns_name.rstrip(".")
    return None
