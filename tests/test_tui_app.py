"""Textual app smoke tests via `App.run_test()`."""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.mp4 import MP4

from musickit import convert as convert_mod


def _make_track(album_dir: Path, silent_flac: Path, *, filename: str, title: str, artist: str) -> Path:
    album_dir.mkdir(parents=True, exist_ok=True)
    dst = album_dir / filename
    convert_mod.to_alac(silent_flac, dst)
    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    tags = mp4.tags
    assert tags is not None
    tags["\xa9nam"] = [title]
    tags["\xa9ART"] = [artist]
    tags["\xa9alb"] = [album_dir.name]
    tags["aART"] = [artist]
    tags["trkn"] = [(1, 1)]
    mp4.save()
    return dst


@pytest.mark.asyncio
async def test_app_populates_browser_with_artists(silent_flac_template: Path, tmp_path: Path) -> None:
    """App boots and populates the browser pane with artist rows at root."""
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    _make_track(
        root / "Imagine Dragons" / "2012 - Night Visions",
        silent_flac_template,
        filename="01 - Radioactive.m4a",
        title="Radioactive",
        artist="Imagine Dragons",
    )
    _make_track(
        root / "Linkin Park" / "2003 - Meteora",
        silent_flac_template,
        filename="01 - Numb.m4a",
        title="Numb",
        artist="Linkin Park",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # Radio is pinned at the top — artists follow.
        assert kinds == ["radio", "artist", "artist"]
        artist_names = sorted(
            str(getattr(c, "entry_data", "")) for c in browser.children if getattr(c, "entry_kind", None) == "artist"
        )
        assert artist_names == ["Imagine Dragons", "Linkin Park"]


@pytest.mark.asyncio
async def test_tracklist_single_click_only_moves_cursor(silent_flac_template: Path, tmp_path: Path) -> None:
    """Single click on a track moves the cursor but does NOT play. Mirrors
    Spotify / iTunes — clicking a row to select it shouldn't restart playback.
    """
    from textual.widgets import ListItem

    from musickit.tui.app import BrowserList, MusickitApp, TrackList

    root = tmp_path / "lib"
    for n in range(1, 4):
        _make_track(
            root / "A" / "2020 - X", silent_flac_template, filename=f"{n:02d} - T{n}.m4a", title=f"T{n}", artist="A"
        )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        a = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(a)  # type: ignore[attr-defined]
        await pilot.pause()
        b = next(c for c in browser.children if getattr(c, "entry_kind", None) == "album")
        pilot.app._handle_browser_selection(b)  # type: ignore[attr-defined]
        await pilot.pause()
        tracklist = pilot.app.query_one(TrackList)
        # Simulate clicking row 2 (index 1) — call the ChildClicked handler
        # directly. A real click bubbles via the same path.
        target = tracklist.children[1]
        assert isinstance(target, ListItem)
        tracklist._on_list_item__child_clicked(ListItem._ChildClicked(target))
        await pilot.pause()
        # Cursor moved, but playback hasn't started: _current_track_idx still None.
        assert tracklist.index == 1
        assert pilot.app._current_track_idx is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tracklist_double_click_plays(silent_flac_template: Path, tmp_path: Path) -> None:
    """Two clicks on the same row within the double-click window play the track."""
    from textual.widgets import ListItem

    from musickit.tui.app import BrowserList, MusickitApp, TrackList

    root = tmp_path / "lib"
    for n in range(1, 4):
        _make_track(
            root / "A" / "2020 - X", silent_flac_template, filename=f"{n:02d} - T{n}.m4a", title=f"T{n}", artist="A"
        )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        a = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(a)  # type: ignore[attr-defined]
        await pilot.pause()
        b = next(c for c in browser.children if getattr(c, "entry_kind", None) == "album")
        pilot.app._handle_browser_selection(b)  # type: ignore[attr-defined]
        await pilot.pause()
        tracklist = pilot.app.query_one(TrackList)
        target = tracklist.children[1]
        assert isinstance(target, ListItem)
        # Two clicks back-to-back on the same row → second is "double".
        tracklist._on_list_item__child_clicked(ListItem._ChildClicked(target))
        tracklist._on_list_item__child_clicked(ListItem._ChildClicked(target))
        await pilot.pause()
        # Selected was posted on second click → `_current_track_idx` is set.
        assert pilot.app._current_track_idx == 1  # type: ignore[attr-defined,unused-ignore]


@pytest.mark.asyncio
async def test_app_quits_on_q(silent_flac_template: Path, tmp_path: Path) -> None:
    """The `q` binding cleanly exits the app."""
    from musickit.tui.app import MusickitApp

    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - X", silent_flac_template, filename="01 - T.m4a", title="T", artist="A")

    async with MusickitApp(root).run_test() as pilot:
        await pilot.press("q")
        # `run_test` exits when the app exits — getting here means it did.


@pytest.mark.asyncio
async def test_app_renders_empty_library(tmp_path: Path) -> None:
    """Empty library → browser shows the Radio entry plus the no-albums note."""
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    root.mkdir()

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # Radio is always present; the second row is the "(no albums)" note.
        assert kinds[0] == "radio"
        assert len(browser.children) == 2


@pytest.mark.asyncio
async def test_app_radio_only_when_no_root_provided(tmp_path: Path) -> None:
    """`musickit tui` (no arg) launches in radio-only mode — no scan, just stations."""
    from musickit.tui.app import BrowserList, MusickitApp

    async with MusickitApp(None).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # Just the Radio entry — no library, no "no albums" note.
        assert kinds == ["radio"]


@pytest.mark.asyncio
async def test_browser_drills_into_artist_and_back(silent_flac_template: Path, tmp_path: Path) -> None:
    """Selecting an artist row replaces the list with that artist's albums.

    Selecting `..` pops back to the artist list.
    """
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    _make_track(
        root / "Imagine Dragons" / "2012 - Night Visions",
        silent_flac_template,
        filename="01 - Radioactive.m4a",
        title="Radioactive",
        artist="Imagine Dragons",
    )
    _make_track(
        root / "Imagine Dragons" / "2015 - Smoke + Mirrors",
        silent_flac_template,
        filename="01 - Shots.m4a",
        title="Shots",
        artist="Imagine Dragons",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        # Find the artist row — Radio is now pinned at the top, so it's [1].
        artist_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(artist_row)  # type: ignore[attr-defined]
        await pilot.pause()
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        assert kinds[0] == "up"
        assert kinds[1:] == ["album", "album"]
        # Pop back via the `..` row.
        pilot.app._handle_browser_selection(browser.children[0])  # type: ignore[attr-defined]
        await pilot.pause()
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # Back at root: radio + the one artist.
        assert kinds == ["radio", "artist"]


@pytest.mark.asyncio
async def test_tick_handlers_safe_after_unmount(silent_flac_template: Path, tmp_path: Path) -> None:
    """Timer ticks fired after the DOM is gone must not raise.
    Regression: `_refresh_visualizer` and friends used to call `query_one`
    unconditionally; if a tick was in-flight when the app unmounted,
    `NoMatches` would propagate and fail other tests in the suite.
    """
    from musickit.tui.app import MusickitApp

    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - X", silent_flac_template, filename="01 - T.m4a", title="T", artist="A")

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        app = pilot.app
    # Outside the context: app has unmounted. Calling the tick handlers
    # directly must not raise.
    app._refresh_visualizer()  # type: ignore[attr-defined]
    app._refresh_status()  # type: ignore[attr-defined]
    app._end_pending = True  # type: ignore[attr-defined]
    app._drain_end_pending()  # type: ignore[attr-defined]
    app._stream_metadata_dirty = True  # type: ignore[attr-defined]
    app._drain_stream_metadata()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pop_browser_restores_correct_artist_cursor(silent_flac_template: Path, tmp_path: Path) -> None:
    """After drilling into an artist + popping out, the cursor lands on that
    artist (not the previous one). Regression: the prepended Radio row used
    to shift the cursor up by one — drilling into Radiohead landed on
    Metallica on pop.
    """
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    for artist in ("Metallica", "Radiohead"):
        _make_track(
            root / artist / "2000 - Album",
            silent_flac_template,
            filename="01 - Track.m4a",
            title="Track",
            artist=artist,
        )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        # Drill into Radiohead (alphabetical: row 0=radio, 1=Metallica, 2=Radiohead).
        radiohead_row = next(c for c in browser.children if getattr(c, "entry_data", None) == "Radiohead")
        pilot.app._handle_browser_selection(radiohead_row)  # type: ignore[attr-defined]
        await pilot.pause()
        # Now pop out via the `..` row.
        pilot.app._handle_browser_selection(browser.children[0])  # type: ignore[attr-defined]
        await pilot.pause()
        # Cursor must land on Radiohead, not Metallica.
        highlighted = browser.highlighted_child
        assert highlighted is not None
        assert getattr(highlighted, "entry_data", None) == "Radiohead"
