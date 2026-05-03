"""Radio-station config loader."""

from __future__ import annotations

from pathlib import Path

from musickit.radio import DEFAULT_STATIONS, RadioStation, load_stations, seed_default_config


def test_load_stations_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    """No user TOML → just the baked-in defaults appear."""
    stations = load_stations(tmp_path / "nope.toml")
    assert [s.url for s in stations] == [d.url for d in DEFAULT_STATIONS]


def test_load_stations_user_entries_appear_before_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text(
        '[[stations]]\nname = "User Station"\nurl = "https://user.example/stream"\n',
        encoding="utf-8",
    )
    stations = load_stations(cfg)
    assert stations[0].url == "https://user.example/stream"
    # Defaults are still present, after the user's.
    default_urls_in_output = [s.url for s in stations[1:]]
    assert default_urls_in_output == [d.url for d in DEFAULT_STATIONS]


def test_load_stations_user_overrides_default_on_url_collision(tmp_path: Path) -> None:
    """A user entry with the same URL as a default silently overrides it."""
    default = DEFAULT_STATIONS[0]
    cfg = tmp_path / "radio.toml"
    cfg.write_text(
        f'[[stations]]\nname = "My Override"\nurl = "{default.url}"\n',
        encoding="utf-8",
    )
    stations = load_stations(cfg)
    # Same number of stations as defaults (no duplicates).
    assert len(stations) == len(DEFAULT_STATIONS)
    # The first entry has the user's name, not the default's.
    assert stations[0].name == "My Override"
    assert stations[0].url == default.url


def test_load_stations_skips_malformed_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text(
        '[[stations]]\nname = "Good"\nurl = "https://good.example/x"\n\n'
        '[[stations]]\nname = "BadNoUrl"\n',  # missing required `url`
        encoding="utf-8",
    )
    user = [s for s in load_stations(cfg) if s.name == "Good"]
    assert len(user) == 1
    assert "BadNoUrl" not in [s.name for s in load_stations(cfg)]


def test_seed_default_config_writes_user_template(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    written = seed_default_config(cfg)
    assert written == cfg
    assert cfg.exists()
    # Template has no actual (uncommented) stations — defaults are in code.
    text = cfg.read_text(encoding="utf-8")
    uncommented_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    assert "[[stations]]" not in "\n".join(uncommented_lines)
    # Loading still returns the defaults (no user entries in template).
    assert [s.url for s in load_stations(cfg)] == [d.url for d in DEFAULT_STATIONS]


def test_seed_default_config_does_not_overwrite_existing(tmp_path: Path) -> None:
    cfg = tmp_path / "radio.toml"
    cfg.write_text('[[stations]]\nname = "Mine"\nurl = "https://example/x"\n', encoding="utf-8")
    seed_default_config(cfg)
    user_only = [s for s in load_stations(cfg) if s.name == "Mine"]
    assert len(user_only) == 1


def test_radio_station_round_trip() -> None:
    s = RadioStation(name="X", url="https://x")
    assert s.name == "X"
    assert s.description is None
