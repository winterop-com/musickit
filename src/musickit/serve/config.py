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


def resolve_credentials(*, cli_user: str | None, cli_password: str | None) -> ServeConfig:
    """CLI flags win over the TOML; either-or, but at least one must yield both fields."""
    file_creds = load_config()
    username = cli_user or file_creds.get("username")
    password = cli_password or file_creds.get("password")
    if not username or not password:
        raise ValueError(
            "missing username/password — provide --user/--password or write "
            f"`username = ...` and `password = ...` to {config_path()}"
        )
    return ServeConfig(username=username, password=password)
