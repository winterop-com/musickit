"""Subsonic internet-radio endpoints, backed by `radio.load_stations()`.

The TUI's `radio.toml` (defaults + user-edited) is the source of truth.
`getInternetRadioStations` exposes that list to Subsonic clients (Symfonium,
Amperfy, the local web UI). Write endpoints (create/update/delete) stay as
success no-ops — stations are managed in the TOML file, not via the API.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from musickit import radio
from musickit.serve.app import envelope
from musickit.serve.radio_proxy import latest_icy_title, proxy_station_stream

router = APIRouter()


def _station_payload() -> list[dict[str, str]]:
    """Render `radio.load_stations()` into Subsonic spec shape."""
    out: list[dict[str, str]] = []
    for i, station in enumerate(radio.load_stations(), start=1):
        item: dict[str, str] = {"id": str(i), "name": station.name, "streamUrl": station.url}
        if station.homepage:
            item["homepageUrl"] = station.homepage
        out.append(item)
    return out


@router.api_route("/getInternetRadioStations", methods=["GET", "POST", "HEAD"])
@router.api_route("/getInternetRadioStations.view", methods=["GET", "POST", "HEAD"])
async def get_internet_radio_stations() -> dict:
    """Return defaults + radio.toml stations in Subsonic shape."""
    return envelope("internetRadioStations", {"internetRadioStation": _station_payload()})


@router.api_route("/createInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/createInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def create_internet_radio_station() -> dict:
    """No-op — stations live in the TUI's local config."""
    return envelope()


@router.api_route("/updateInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/updateInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def update_internet_radio_station() -> dict:
    """No-op — stations live in the TUI's local config."""
    return envelope()


@router.api_route("/deleteInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/deleteInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def delete_internet_radio_station() -> dict:
    """No-op — stations live in the TUI's local config."""
    return envelope()


@router.api_route("/radioStream", methods=["GET", "POST", "HEAD"])
@router.api_route("/radioStream.view", methods=["GET", "POST", "HEAD"])
async def radio_stream(url: str) -> Response:
    """Same-origin proxy for a radio station's upstream stream (Subsonic-auth'd).

    Non-standard MusicKit extension. Designed for the desktop wrappers
    (Tauri / Electron): their `<audio>` element keeps `crossOrigin =
    "anonymous"` so the visualizer can read FFT samples, which means
    any cross-origin `audio.src` triggers a CORS preflight. Icecast /
    SHOUTcast stations don't return CORS headers, so direct playback
    fails silently. Routing the stream through this endpoint adds the
    server's open CORS headers and audio loads. Same allowlist gate
    as `/web/radio-stream` (URL must be in `radio.load_stations()`).
    """
    return await proxy_station_stream(url)


@router.api_route("/radioMeta", methods=["GET", "POST", "HEAD"])
@router.api_route("/radioMeta.view", methods=["GET", "POST", "HEAD"])
async def radio_meta(url: str) -> Response:
    """Return the last-seen ICY StreamTitle for a station URL, parsed by the proxy.

    Cheap polling endpoint mirroring `/web/radio-meta` for Subsonic clients.
    Returns `{"title": ""}` when the station hasn't sent metadata yet.
    """
    return JSONResponse({"title": latest_icy_title(url)})
