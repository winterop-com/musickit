"""Server config — `~/.config/musickit/serve.toml` with user + password.

The TOML lives next to `radio.toml` and `state.json`. CLI flags (`--user`,
`--password`) override the file values; either source must produce a
non-empty username/password or the server refuses to start.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class ServeConfig(BaseModel):
    """Resolved server credentials. Plain text — this is local-self-hosted."""

    username: str
    password: str


def config_path() -> Path:
    """`~/.config/musickit/serve.toml` — same dir as the TUI's radio + state files."""
    return Path.home() / ".config" / "musickit" / "serve.toml"


def load_config() -> dict[str, str]:
    """Read serve.toml. Returns `{}` if missing or malformed."""
    p = config_path()
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, str] = {}
    for key in ("username", "password"):
        value = data.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"  # noqa: S105 — default for first-run convenience; warning emitted when used


def resolve_credentials(*, cli_user: str | None, cli_password: str | None) -> tuple[ServeConfig, bool]:
    """CLI flags win over the TOML. Falls back to admin/admin when nothing is set.

    Returns `(cfg, used_defaults)` so the caller can warn the user when the
    insecure defaults are in play.
    """
    file_creds = load_config()
    username = cli_user or file_creds.get("username") or DEFAULT_USERNAME
    password = cli_password or file_creds.get("password") or DEFAULT_PASSWORD
    used_defaults = username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD
    return ServeConfig(username=username, password=password), used_defaults
