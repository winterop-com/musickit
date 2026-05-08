"""Server config — Subsonic credentials + scrobble forwarder shape.

The TOML home moved to `~/.config/musickit/musickit.toml [server]` in
v0.11; the legacy `~/.config/musickit/serve.toml` is read as a fallback
via `musickit.config.load_config()`. CLI flags (`--user`, `--password`)
still override the file values; defaults are admin/admin with a yellow
warning printed at startup so a fresh install doesn't sit anonymous on
the LAN.

Optional `[scrobble.webhook]` and `[scrobble.mqtt]` blocks turn the
`/scrobble` endpoint from a no-op stub into a forwarder — see
`serve/scrobble.py`.
"""

from __future__ import annotations

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


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"  # noqa: S105 — default for first-run convenience; warning emitted when used


def resolve_credentials(*, cli_user: str | None, cli_password: str | None) -> tuple[ServeConfig, bool]:
    """CLI flags win over env vars / TOML. Falls back to admin/admin when nothing is set.

    Returns `(cfg, used_defaults)` so the caller can warn the user when
    the insecure defaults are in play. Reads the consolidated config via
    `musickit.config.load_config` (which itself falls back to the legacy
    `serve.toml` for one release cycle).
    """
    from musickit.config import load_config

    cfg = load_config()
    username = cli_user or cfg.server.username
    password = cli_password or cfg.server.password
    used_defaults = username == DEFAULT_USERNAME and password == DEFAULT_PASSWORD
    return ServeConfig(username=username, password=password, scrobble=cfg.server.scrobble), used_defaults
