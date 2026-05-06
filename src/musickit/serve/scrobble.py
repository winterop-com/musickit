"""Scrobble forwarder — webhook + MQTT publish on every `/scrobble` call.

Subsonic clients (Symfonium, Amperfy, Feishin) fire `/scrobble` once per
track played. The default endpoint is a no-op accept-and-discard. With
`[scrobble.webhook]` or `[scrobble.mqtt]` configured in `serve.toml`,
each scrobble becomes a structured event posted/published to whatever
the user has wired up — Last.fm bridge, ListenBrainz, Home Assistant
automation, custom analytics, etc.

Forwarding is fire-and-forget: each scrobble dispatch runs on a small
thread pool, errors are logged and swallowed. A dead webhook URL or
flaky broker must not 500 the client's `/scrobble` request — clients
treat scrobble failures as a "library is broken" signal and back off.

The MQTT path lazy-imports `paho-mqtt` so users without that dep on the
install path still get the webhook half. paho-mqtt is in the runtime
deps list but the import inside the publisher means a missing-package
case logs once and disables MQTT instead of crashing the process.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from musickit.serve.config import ScrobbleConfig, ScrobbleMqttConfig, ScrobbleWebhookConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrobbleEvent:
    """Structured payload emitted to webhook / MQTT.

    Field naming mirrors the Subsonic spec's scrobble parameters plus a
    couple resolved-from-cache fields (artist / title / album) so the
    receiver doesn't have to round-trip back to the server to look them
    up.
    """

    user: str
    track_id: str
    title: str
    artist: str
    album: str
    duration_s: float
    played_at: str  # ISO-8601 UTC
    submission: bool  # True = "I finished playing this", False = "I just started"

    def as_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


class ScrobbleDispatcher:
    """Fan a `ScrobbleEvent` out to the configured webhook + MQTT targets.

    One dispatcher per `serve` process. Holds a small thread pool so a
    blocked webhook target doesn't tie up the request handler.
    """

    def __init__(self, cfg: ScrobbleConfig, *, http: httpx.Client | None = None) -> None:
        self._cfg = cfg
        self._owns_http = http is None
        self._http = http or httpx.Client(timeout=cfg.webhook.timeout_s if cfg.webhook else 5.0)
        # max_workers=2 — enough for one webhook + one MQTT in parallel,
        # bounded so a flood of scrobbles can't fork-bomb threads.
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="musickit-scrobble")
        self._mqtt_client: Any = None
        self._mqtt_lock = threading.Lock()
        self._mqtt_disabled = False  # set True after a one-time import failure

    def dispatch(self, event: ScrobbleEvent) -> None:
        """Send `event` to every configured target. Returns immediately."""
        if not self._cfg.enabled:
            return
        if event.submission is False and not self._cfg.include_now_playing:
            return
        if self._cfg.webhook is not None:
            self._pool.submit(self._post_webhook, self._cfg.webhook, event)
        if self._cfg.mqtt is not None and not self._mqtt_disabled:
            self._pool.submit(self._publish_mqtt, self._cfg.mqtt, event)

    def shutdown(self) -> None:
        """Wait briefly for in-flight dispatches, then close clients. Idempotent."""
        self._pool.shutdown(wait=True, cancel_futures=False)
        if self._owns_http:
            try:
                self._http.close()
            except Exception:  # pragma: no cover — best effort
                pass
        client = self._mqtt_client
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:  # pragma: no cover — best effort
                pass

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    def _post_webhook(self, cfg: ScrobbleWebhookConfig, event: ScrobbleEvent) -> None:
        """POST the event JSON. Failures are logged at WARNING level and swallowed."""
        headers = {"Content-Type": "application/json", "User-Agent": "musickit-scrobble/1.0"}
        if cfg.secret:
            headers["X-Musickit-Secret"] = cfg.secret
        try:
            self._http.post(cfg.url, content=event.as_json(), headers=headers, timeout=cfg.timeout_s)
        except httpx.HTTPError as exc:
            log.warning("scrobble webhook failed: %s", exc)

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------

    def _publish_mqtt(self, cfg: ScrobbleMqttConfig, event: ScrobbleEvent) -> None:
        client = self._ensure_mqtt_client(cfg)
        if client is None:
            return
        try:
            client.publish(cfg.topic, payload=event.as_json(), qos=0, retain=False)
        except Exception as exc:  # noqa: BLE001 — paho can raise a wide set under flaky conditions
            log.warning("scrobble mqtt publish failed: %s", exc)

    def _ensure_mqtt_client(self, cfg: ScrobbleMqttConfig) -> Any:
        """Lazy-init the MQTT client; return None if paho isn't installed."""
        with self._mqtt_lock:
            if self._mqtt_client is not None:
                return self._mqtt_client
            if self._mqtt_disabled:
                return None
            try:
                import paho.mqtt.client as paho_mqtt
            except ImportError as exc:
                log.warning("paho-mqtt not installed; mqtt scrobble forwarder disabled (%s)", exc)
                self._mqtt_disabled = True
                return None

            host, port = _parse_broker(cfg.broker)
            try:
                client = paho_mqtt.Client(client_id=cfg.client_id, clean_session=True)
                if cfg.username:
                    client.username_pw_set(cfg.username, cfg.password or "")
                client.connect_async(host, port)
                client.loop_start()
            except Exception as exc:  # noqa: BLE001 — paho throws OSError / ValueError on bad broker
                log.warning("scrobble mqtt connect failed (%s); disabling", exc)
                self._mqtt_disabled = True
                return None
            self._mqtt_client = client
            return client


def _parse_broker(broker: str) -> tuple[str, int]:
    """Parse `mqtt://host:port` or `host:port` or `host` (default port 1883)."""
    text = broker.strip()
    if "://" not in text:
        text = "mqtt://" + text
    parsed = urlparse(text)
    host = parsed.hostname or "localhost"
    port = parsed.port or 1883
    return host, port
