"""TUI Mixes browser entry — saved .m3u8 listing + playback.

The flow under test:
  1. Generate a mix via `g` so a .m3u8 lands on disk.
  2. Drill out, drop into the Mixes browser entry.
  3. Confirm the saved .m3u8 appears in the right-pane TrackList.
  4. Select it; assert it loads as a virtual album with the resolved tracks.

`_load_mix` is also tested directly: gracefully degrades when paths in
the .m3u8 no longer exist.
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
    app = pilot.app
    assert isinstance(app, MusickitApp)
    browser = app.query_one(BrowserList)
    artist_row = await wait_for_browser_child(
        pilot,
        lambda: browser.children,
        lambda c: getattr(c, "entry_data", None) == artist,
        description=f"artist row {artist!r}",
    )
    assert isinstance(artist_row, ListItem)
    app._handle_browser_selection(artist_row)
    await pilot.pause()
    album_row = browser.children[1]
    assert isinstance(album_row, ListItem)
    app._handle_browser_selection(album_row)
    await pilot.pause()


def _stage_lib(tmp_path: Path, silent_flac_template: Path) -> Path:
    """Two-album library so the generated mix has somewhere to go."""
    root = tmp_path / "lib"
    a1 = root / "Pixies" / "1989 - Doolittle"
    a2 = root / "Pixies" / "1990 - Bossanova"
    _make_track(a1, silent_flac_template, filename="01 - Debaser.m4a", title="Debaser", artist="Pixies")
    _make_track(a1, silent_flac_template, filename="02 - Tame.m4a", title="Tame", artist="Pixies", track_no=2)
    _make_track(a2, silent_flac_template, filename="01 - Cecilia Ann.m4a", title="Cecilia Ann", artist="Pixies")
    return root


@pytest.mark.asyncio
async def test_mixes_browser_entry_lists_saved_m3u8(silent_flac_template: Path, tmp_path: Path) -> None:
    """After `g` writes a .m3u8, the Mixes view shows it as a TrackList row."""
    root = _stage_lib(tmp_path, silent_flac_template)
    app = MusickitApp(root)

    async with app.run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, "Pixies")
        a = pilot.app
        assert isinstance(a, MusickitApp)

        # Generate one mix.
        tracklist = a.query_one(TrackList)
        tracklist.focus()
        tracklist.index = 0
        await pilot.pause()
        a.action_generate_playlist()
        await pilot.pause()

        # Pop back to the artists list so Mixes is visible.
        a._browse_artist = None
        a._populate_browser()
        await pilot.pause()
        browser = a.query_one(BrowserList)
        mixes_row = await wait_for_browser_child(
            pilot,
            lambda: browser.children,
            lambda c: getattr(c, "entry_kind", None) == "mixes",
            description="mixes row",
        )
        assert isinstance(mixes_row, ListItem)
        a._handle_browser_selection(mixes_row)
        await pilot.pause()

        assert a._in_mixes_view is True
        # TrackList shows the .m3u8 file as a row carrying `mix_path`.
        rows = a.query_one(TrackList)
        mix_paths: list[Path] = [
            p for p in (getattr(r, "mix_path", None) for r in rows.children) if isinstance(p, Path)
        ]
        assert len(mix_paths) == 1
        assert mix_paths[0].suffix == ".m3u8"


@pytest.mark.asyncio
async def test_play_mix_loads_virtual_album_from_saved_file(silent_flac_template: Path, tmp_path: Path) -> None:
    """Selecting a mix row resolves its tracks and starts playback."""
    root = _stage_lib(tmp_path, silent_flac_template)
    app = MusickitApp(root)

    async with app.run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, "Pixies")
        a = pilot.app
        assert isinstance(a, MusickitApp)

        # Make a mix so there's something on disk.
        tracklist = a.query_one(TrackList)
        tracklist.focus()
        tracklist.index = 0
        await pilot.pause()
        a.action_generate_playlist()
        await pilot.pause()

        pdir = root / library_mod.INDEX_DIR_NAME / "playlists"
        files = list(pdir.glob("*.m3u8"))
        assert files, "expected at least one saved mix"

        # Drop straight into _play_mix (the same call site `_handle_browser_selection`
        # ends up making for a mix row).
        a._play_mix(files[0])
        await pilot.pause()

        assert a._current_album is not None
        assert a._current_album.artist_dir == "Mix"
        assert a._current_album.album_dir == files[0].stem
        assert len(a._current_album.tracks) >= 1


def test_load_mix_skips_missing_paths(silent_flac_template: Path, tmp_path: Path) -> None:
    """Tracks listed in the .m3u8 that no longer exist are silently skipped."""
    root = tmp_path / "lib"
    track_path = _make_track(
        root / "A" / "2020 - One",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="A",
    )

    app = MusickitApp(root)

    # We don't need to start the App; _load_mix only touches `self._index`,
    # so seed it directly via load_or_scan.
    app._index = library_mod.load_or_scan(root, use_cache=False)

    pdir = root / library_mod.INDEX_DIR_NAME / "playlists"
    pdir.mkdir(parents=True)
    m3u = pdir / "broken.m3u8"
    m3u.write_text(
        f"#EXTM3U\n#EXTINF:180,A - T\n{track_path}\n#EXTINF:200,Ghost - Vanished\n/does/not/exist.m4a\n",
        encoding="utf-8",
    )

    album = app._load_mix(m3u)
    assert album is not None
    # One track survives, one was skipped because its path is gone.
    assert len(album.tracks) == 1
    assert album.tracks[0].path == track_path


def test_load_mix_returns_none_when_all_paths_gone(silent_flac_template: Path, tmp_path: Path) -> None:
    """Saved mix that references only-missing files returns None."""
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    app = MusickitApp(root)
    app._index = library_mod.load_or_scan(root, use_cache=False)

    pdir = root / library_mod.INDEX_DIR_NAME / "playlists"
    pdir.mkdir(parents=True)
    m3u = pdir / "all-gone.m3u8"
    m3u.write_text("#EXTM3U\n/no/where.m4a\n/also/gone.m4a\n", encoding="utf-8")

    assert app._load_mix(m3u) is None


# Silence pytest unused-import noise for fixtures used via direct invocation.
_ = pytest
