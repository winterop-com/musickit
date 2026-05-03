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
            help="Library root to expose. Defaults to ./output.",
        ),
    ] = Path("./output"),
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
    # cache. Big libraries take seconds to walk; the banner above tells the
    # user it's happening.
    typer.echo("scanning library…")
    fastapi_app.state.cache.rebuild()
    cache = fastapi_app.state.cache
    typer.echo(f"  {cache.artist_count} artists, {cache.album_count} albums, {cache.track_count} tracks\n")

    import uvicorn

    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


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
