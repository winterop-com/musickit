"""Radio-station config loader."""

from __future__ import annotations

from pathlib import Path

from musickit.radio import RadioStation, load_stations, seed_default_config


def test_load_stations_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert load_stations(tmp_path / "nope.toml") == []


def test_load_stations_parses_array_of_tables(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text(
        '[[stations]]\nname = "Station A"\nurl = "https://a.example/stream"\n\n'
        '[[stations]]\nname = "Station B"\nurl = "https://b.example/stream"\ndescription = "Test"\n',
        encoding="utf-8",
    )
    stations = load_stations(cfg)
    assert [s.name for s in stations] == ["Station A", "Station B"]
    assert stations[1].description == "Test"


def test_load_stations_skips_malformed_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text(
        '[[stations]]\nname = "Good"\nurl = "https://x"\n\n[[stations]]\nname = "BadNoUrl"\n',  # missing required `url`
        encoding="utf-8",
    )
    stations = load_stations(cfg)
    assert [s.name for s in stations] == ["Good"]


def test_seed_default_config_writes_starter_stations(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    written = seed_default_config(cfg)
    assert written == cfg
    assert cfg.exists()
    stations = load_stations(cfg)
    assert len(stations) >= 1
    assert all(s.url.startswith("http") for s in stations)


def test_seed_default_config_does_not_overwrite_existing(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text('[[stations]]\nname = "Mine"\nurl = "https://example/x"\n', encoding="utf-8")
    seed_default_config(cfg)
    stations = load_stations(cfg)
    assert [s.name for s in stations] == ["Mine"]


def test_radio_station_round_trip() -> None:
    s = RadioStation(name="X", url="https://x")
    assert s.name == "X"
    assert s.description is None
