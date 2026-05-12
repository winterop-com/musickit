"""`musickit ui` — local web UI that points at any Subsonic server.

Unlike `musickit serve` (which is itself a Subsonic server), `musickit ui`
is purely a client. It static-serves the desktop SPA — the same
HTML/JS/CSS the Tauri and Electron wrappers load — on a local port, then
opens a browser tab against it. The SPA's login picker asks for a server
URL + credentials and talks to any spec-compliant Subsonic server
(musickit serve, Navidrome, Airsonic, Gonic, ...).

Useful when:
  - You want a browser UI against a remote musickit serve without
    running a second copy locally.
  - You want to evaluate musickit's web UI against your existing
    Navidrome / Airsonic / Gonic library without installing the
    desktop wrappers.

Optional `--url / --user / --password` arguments pre-fill the picker
form via a query string so a one-command "open the UI pointed at X"
works without typing.
"""

from __future__ import annotations

import webbrowser
from importlib.resources import files
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import typer
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from musickit.cli import app


def _resolve_static_dir() -> Path:
    """Resolve the SPA static directory.

    Two layouts to support:
      - PyPI install: `src/musickit/_ui_static/` (copied in by
        `scripts/copy_ui_static.py` at `make build` time).
      - Source checkout: `desktop/react/` from the repo root — useful
        for `uv run musickit ui` during development before the bundle
        has been generated.
    """
    bundled = files("musickit") / "_ui_static"
    bundled_path = Path(str(bundled))
    if bundled_path.is_dir() and (bundled_path / "index.html").exists():
        return bundled_path
    here = Path(__file__).resolve()
    # ui.py -> cli/ -> musickit/ -> src/ -> repo root
    repo_root = here.parents[3]
    dev_dir = repo_root / "desktop" / "react"
    if dev_dir.is_dir() and (dev_dir / "index.html").exists():
        return dev_dir
    raise FileNotFoundError(
        "Couldn't find the SPA static files. Either install musickit via pip "
        "(the wheel bundles them) or run from a source checkout where "
        "`desktop/react/` is present."
    )


@app.command()
def ui(
    host: Annotated[
        str,
        typer.Option("--host", help="Interface to bind. Default 127.0.0.1 (local only)."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Port to bind. Default 1888."),
    ] = 1888,
    url: Annotated[
        str | None,
        typer.Option("--url", help="Pre-fill the Subsonic server URL (e.g. http://macair:4533)."),
    ] = None,
    user: Annotated[
        str | None,
        typer.Option("--user", help="Pre-fill the username."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="Pre-fill the password."),
    ] = None,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Skip auto-opening the browser tab."),
    ] = False,
) -> None:
    """Serve the desktop SPA locally as a client for any Subsonic server.

    The SPA accepts server URL + credentials at login and talks to any
    spec-compliant Subsonic server. No `musickit serve` instance is
    required; this command is just a local static-file server for the
    same HTML/JS the desktop wrappers ship.
    """
    static_dir = _resolve_static_dir()
    server = FastAPI()
    server.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")

    params: dict[str, str] = {}
    if url:
        params["host"] = url
    if user:
        params["user"] = user
    if password:
        params["password"] = password
    target = f"http://{host}:{port}/"
    if params:
        target += "?" + urlencode(params)

    print(f">>> musickit ui — static files: {static_dir}")
    print(f">>> Open this in a browser: {target}")
    if not no_open:
        webbrowser.open(target)
    # Cast: uvicorn.run accepts an `Application` interface, FastAPI implements it.
    uvicorn.run(server, host=host, port=port, log_level="warning")
