"""Subsonic internet-radio endpoints, backed by `radio.load_stations()`.

The TUI's `radio.toml` (defaults + user-edited) is the source of truth.
`getInternetRadioStations` exposes that list to Subsonic clients (Symfonium,
Amperfy, the local web UI). Write endpoints (create/update/delete) stay as
success no-ops — stations are managed in the TOML file, not via the API.
"""

from __future__ import annotations

from fastapi import APIRouter

from musickit import radio
from musickit.serve.app import envelope

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
