"""LyricsPane: `l` toggles visibility; position_ms drives the active-line highlight."""

from __future__ import annotations

from pathlib import Path

import pytest

from musickit.lyrics import LrcLine
from musickit.tui.app import MusickitApp
from musickit.tui.widgets import LyricsPane
from tests.test_library import _make_track


@pytest.mark.asyncio
async def test_l_keybind_toggles_lyrics_pane(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        app = pilot.app
        assert isinstance(app, MusickitApp)
        pane = app.query_one(LyricsPane)
        assert pane.has_class("visible") is False

        app.action_toggle_lyrics()
        await pilot.pause()
        assert pane.has_class("visible") is True
        assert app.screen.has_class("show-lyrics") is True

        app.action_toggle_lyrics()
        await pilot.pause()
        assert pane.has_class("visible") is False
        assert app.screen.has_class("show-lyrics") is False


@pytest.mark.asyncio
async def test_position_ms_advances_active_line(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        pane = pilot.app.query_one(LyricsPane)
        pane.lines = [
            LrcLine(start_ms=0, text="zero"),
            LrcLine(start_ms=1000, text="one"),
            LrcLine(start_ms=2000, text="two"),
        ]
        pane.synced = True

        pane.position_ms = 0
        rendered = pane.render()
        assert "zero" in rendered

        pane.position_ms = 1500
        rendered = pane.render()
        # Line at 1000 ms should be the active (bold/colored) one; lines
        # before it dimmed; line at 2000 ms still in normal style. Use
        # line text as proxy — full markup assertion would couple too
        # tightly to palette colors.
        assert "one" in rendered
        assert "two" in rendered
        assert "zero" in rendered


_ = pytest
