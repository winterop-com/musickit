"""`musickit inspect` — pretty-printed tag dump for one audio file."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from musickit.cli import app
from tests.test_library import _make_track


def test_inspect_renders_pretty_panels(silent_flac_template: Path, tmp_path: Path) -> None:
    """Default render shows the File / Tags panels with the expected fields."""
    track = _make_track(
        tmp_path / "Artist" / "2020 - Album",
        silent_flac_template,
        filename="01 - Track.m4a",
        title="Selfmachine",
        artist="I Blame Coco",
        album="Absolute Music 65",
        year="2010",
        track_no=1,
        track_total=12,
        disc_no=1,
        disc_total=2,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(track)])
    assert result.exit_code == 0, result.output
    # Panels are present.
    assert "File" in result.output
    assert "Tags" in result.output
    # Tag values land in the table.
    assert "Selfmachine" in result.output
    assert "I Blame Coco" in result.output
    assert "Absolute Music 65" in result.output
    # Composite track / disc display: "1/12" and "1/2".
    assert "1/12" in result.output
    assert "1/2" in result.output


def test_inspect_json_flag_keeps_raw_output(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--json` falls back to the raw model dump for scripting."""
    track = _make_track(
        tmp_path / "Artist" / "2020 - Album",
        silent_flac_template,
        filename="01 - T.m4a",
        title="A",
        artist="B",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(track), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["title"] == "A"
    assert payload["artist"] == "B"
    # Embedded picture bytes are excluded from the JSON form.
    assert "embedded_picture" not in payload


def test_inspect_shows_picture_panel_when_embedded(silent_flac_template: Path, tmp_path: Path) -> None:
    """A track with a cover gets the dedicated Embedded picture panel."""
    track = _make_track(
        tmp_path / "Artist" / "2020 - Album",
        silent_flac_template,
        filename="01 - T.m4a",
        cover_size=(800, 800),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(track)])
    assert result.exit_code == 0, result.output
    assert "Embedded picture" in result.output
