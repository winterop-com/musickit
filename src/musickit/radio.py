"""Curated internet-radio station list, loaded from `~/.config/musickit/radio.toml`.

Stations are simple `(name, url[, description, homepage])` records. The TOML
file uses the `[[stations]]` array-of-tables form so it stays easy to hand-edit:

    [[stations]]
    name = "SomaFM — Groove Salad"
    url = "https://ice1.somafm.com/groovesalad-128-mp3"
    description = "Ambient downtempo grooves"

If the config file doesn't exist, an empty list is returned (the TUI shows a
friendly empty state). `seed_default_config` writes a starter file with a
couple of working stations so `musickit tui` has something to pick from on
first run.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class RadioStation(BaseModel):
    """One curated streaming source."""

    name: str
    url: str
    description: str | None = None
    homepage: str | None = None


def stations_path() -> Path:
    """Default location of the user's `radio.toml`."""
    return Path.home() / ".config" / "musickit" / "radio.toml"


def load_stations(path: Path | None = None) -> list[RadioStation]:
    """Read the user's curated station list. Returns `[]` if the file is missing."""
    target = path or stations_path()
    if not target.exists():
        return []
    with target.open("rb") as f:
        data = tomllib.load(f)
    raw = data.get("stations") or []
    stations: list[RadioStation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            stations.append(RadioStation(**entry))
        except (TypeError, ValueError):
            continue
    return stations


_DEFAULT_STATIONS_TOML = """\
# musickit radio stations — edit this file by hand to add / remove sources.
# Each entry needs at least a `name` and `url`. Restart the TUI (or press
# Ctrl+R) to pick up changes.

[[stations]]
name = "NRK mP3"
url = "https://lyd.nrk.no/icecast/aac/high/s0w7hwn47m/mp3"
description = "NRK's pop / hits station"
homepage = "https://radio.nrk.no/direkte/mp3"

[[stations]]
name = "NRK P3"
url = "https://lyd.nrk.no/icecast/aac/high/s0w7hwn47m/p3"
description = "NRK P3 — youth talk + music"
homepage = "https://radio.nrk.no/direkte/p3"

[[stations]]
name = "NRK P3 Musikk"
url = "https://lyd.nrk.no/icecast/aac/high/s0w7hwn47m/p3musikk"
description = "P3-style music, no talk"
homepage = "https://radio.nrk.no/direkte/p3musikk"
"""


def seed_default_config(path: Path | None = None) -> Path:
    """Write a starter `radio.toml` if none exists. Returns the path."""
    target = path or stations_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_DEFAULT_STATIONS_TOML, encoding="utf-8")
    return target
