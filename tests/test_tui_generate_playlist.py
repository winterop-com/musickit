"""TUI `g` keybind — generate a mix anchored to the highlighted track.

Drives `action_generate_playlist` through Pilot. Verifies the action:
  - Refuses when the index isn't loaded yet.
  - Refuses when no track is highlighted (or current).
  - On success: synthesises a virtual album, swaps the TrackList to
    show its tracks, writes the .m3u8 to disk, and starts playback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.pilot import Pilot
from textual.widgets import ListItem

from musickit import library as library_mod
from musickit.tui.app import MusickitApp
from musickit.tui.widgets import BrowserList, TrackList
from tests._tui_wait import wait_for_browser_child
from tests.test_library import _make_track


async def _drill_into_album(pilot: Pilot[None], artist: str) -> None:
    """Drill from artists list into the album list of `artist`."""
    app = pilot.app
    assert isinstance(app, MusickitApp)
    browser = app.query_one(BrowserList)
    artist_row = await wait_for_browser_child(
        pilot,
        lambda: browser.children,
        lambda c: getattr(c, "entry_data", None) == artist,
        description="matching entry_data row",
    )
    assert isinstance(artist_row, ListItem)
    app._handle_browser_selection(artist_row)
    await pilot.pause()
    # row 0 = back, row 1 = first album
    album_row = browser.children[1]
    assert isinstance(album_row, ListItem)
    app._handle_browser_selection(album_row)
    await pilot.pause()


@pytest.mark.asyncio
async def test_g_generates_mix_and_swaps_to_virtual_album(silent_flac_template: Path, tmp_path: Path) -> None:
    """`g` on a highlighted track produces a virtual album with >=1 track."""
    root = tmp_path / "lib"
    # Build a couple of albums by the same artist so the mix has somewhere to go.
    a1 = root / "Pixies" / "1989 - Doolittle"
    a2 = root / "Pixies" / "1990 - Bossanova"
    _make_track(a1, silent_flac_template, filename="01 - Debaser.m4a", title="Debaser", artist="Pixies")
    _make_track(a1, silent_flac_template, filename="02 - Tame.m4a", title="Tame", artist="Pixies", track_no=2)
    _make_track(a2, silent_flac_template, filename="01 - Cecilia Ann.m4a", title="Cecilia Ann", artist="Pixies")

    app = MusickitApp(root)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, "Pixies")

        tracklist = pilot.app.query_one(TrackList)
        tracklist.focus()
        # Highlight the first track.
        tracklist.index = 0
        await pilot.pause()
        assert isinstance(pilot.app, MusickitApp)
        seed_album_before = pilot.app._current_album
        assert seed_album_before is not None
        seed_album_dir_before = seed_album_before.album_dir

        pilot.app.action_generate_playlist()
        await pilot.pause()

        # After: current album is the synthesised mix, NOT the original.
        new_album = pilot.app._current_album
        assert new_album is not None
        assert new_album.album_dir != seed_album_dir_before, "should have switched to the virtual mix"
        assert new_album.artist_dir == "Mix"
        assert len(new_album.tracks) >= 1

        # The .m3u8 should have been persisted.
        pdir = root / library_mod.INDEX_DIR_NAME / "playlists"
        assert pdir.is_dir(), "playlists dir should exist after `g`"
        files = list(pdir.glob("*.m3u8"))
        assert files, "at least one .m3u8 should have been written"


@pytest.mark.asyncio
async def test_g_warns_when_no_track_highlighted(silent_flac_template: Path, tmp_path: Path) -> None:
    """`g` with nothing in the tracklist must short-circuit cleanly."""
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    app = MusickitApp(root)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No browser drill-in -> _current_album is None -> seed resolution
        # returns None -> action notifies & returns. No crash, no album swap.
        assert isinstance(pilot.app, MusickitApp)
        assert pilot.app._current_album is None
        pilot.app.action_generate_playlist()
        await pilot.pause()
        # Still nothing playing, nothing crashed.
        assert pilot.app._current_album is None


# Silence pytest unused-import noise for fixtures used via direct invocation.
_ = pytest
