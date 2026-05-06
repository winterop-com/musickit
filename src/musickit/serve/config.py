"""Server config — `~/.config/musickit/serve.toml` with user + password + scrobble forwarders.

The TOML lives next to `radio.toml` and `state.toml`. CLI flags (`--user`,
`--password`) override the file values; either source must produce a
non-empty username/password or the server refuses to start.

Optional `[scrobble.webhook]` and `[scrobble.mqtt]` blocks turn the
`/scrobble` endpoint from a no-op stub into a forwarder — see
`serve/scrobble.py`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ScrobbleWebhookConfig(BaseModel):
    """POST a JSON play event to `url`. Optional `secret` sent as `X-Musickit-Secret` header."""

    url: str
    secret: str | None = None
    timeout_s: float = 5.0


class ScrobbleMqttConfig(BaseModel):
    """Publish play events to an MQTT broker (e.g. for Home Assistant)."""

    broker: str  # `mqtt://host:port` or `host:port`
    topic: str = "musickit/scrobble"
    username: str | None = None
    password: str | None = None
    client_id: str = "musickit"


class ScrobbleConfig(BaseModel):
    """Optional scrobble forwarders. All fields are individually optional."""

    webhook: ScrobbleWebhookConfig | None = None
    mqtt: ScrobbleMqttConfig | None = None
    # Forward only `submission=true` scrobbles by default (the "track
    # finished" event), not the "now playing" probe Subsonic clients fire
    # at the start of a track. Set to True to forward both kinds; useful
    # for Home Assistant "currently playing" automations.
    include_now_playing: bool = False

    @property
    def enabled(self) -> bool:
        return self.webhook is not None or self.mqtt is not None


class ServeConfig(BaseModel):
    """Resolved server credentials + scrobble settings. Plain text — local-self-hosted."""

    username: str
    password: str
    scrobble: ScrobbleConfig = ScrobbleConfig()


def config_path() -> Path:
    """`~/.config/musickit/serve.toml` — same dir as the TUI's radio + state files."""
    return Path.home() / ".config" / "musickit" / "serve.toml"


def load_config() -> dict[str, Any]:
    """Read serve.toml. Returns `{}` if missing or malformed.

    Returns the full nested dict so callers (resolve_credentials, scrobble
    config) can pick out the parts they need.
    """
    p = config_path()
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    # `tomllib.load` already returns a dict at the top level — annotation
    # makes that explicit so downstream callers don't need a defensive check.
    return data


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"  # noqa: S105 — default for first-run convenience; warning emitted when used


def resolve_credentials(*, cli_user: str | None, cli_password: str | None) -> tuple[ServeConfig, bool]:
    """CLI flags win over the TOML. Falls back to admin/admin when nothing is set.

    Returns `(cfg, used_defaults)` so the caller can warn the user when the
    insecure defaults are in play.
    """
    file_data = load_config()
    file_creds: dict[str, str] = {}
    for key in ("username", "password"):
        value = file_data.get(key)
        if isinstance(value, str) and value:
            file_creds[key] = value

    username = cli_user or file_creds.get("username") or DEFAULT_USERNAME
    password = cli_password or file_creds.get("password") or DEFAULT_PASSWORD
    used_defaults = username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD

    scrobble = _parse_scrobble(file_data.get("scrobble"))
    return ServeConfig(username=username, password=password, scrobble=scrobble), used_defaults


def _parse_scrobble(raw: Any) -> ScrobbleConfig:
    """Build a `ScrobbleConfig` from the `[scrobble]` TOML block, tolerating partial / missing data."""
    if not isinstance(raw, dict):
        return ScrobbleConfig()
    webhook = _parse_webhook(raw.get("webhook"))
    mqtt = _parse_mqtt(raw.get("mqtt"))
    include_now_playing = bool(raw.get("include_now_playing", False))
    return ScrobbleConfig(webhook=webhook, mqtt=mqtt, include_now_playing=include_now_playing)


def _parse_webhook(raw: Any) -> ScrobbleWebhookConfig | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        return None
    secret = raw.get("secret")
    timeout = raw.get("timeout_s", 5.0)
    return ScrobbleWebhookConfig(
        url=url,
        secret=secret if isinstance(secret, str) and secret else None,
        timeout_s=float(timeout) if isinstance(timeout, (int, float)) else 5.0,
    )


def _parse_mqtt(raw: Any) -> ScrobbleMqttConfig | None:
    if not isinstance(raw, dict):
        return None
    broker = raw.get("broker")
    if not isinstance(broker, str) or not broker:
        return None
    topic = raw.get("topic")
    client_id = raw.get("client_id")
    return ScrobbleMqttConfig(
        broker=broker,
        topic=topic if isinstance(topic, str) and topic else "musickit/scrobble",
        username=raw.get("username") if isinstance(raw.get("username"), str) else None,
        password=raw.get("password") if isinstance(raw.get("password"), str) else None,
        client_id=client_id if isinstance(client_id, str) and client_id else "musickit",
    )
