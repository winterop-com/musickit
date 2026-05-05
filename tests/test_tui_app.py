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
        # Radio + Mixes are pinned at the top — artists follow.
        assert kinds == ["radio", "mixes", "artist", "artist"]
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
async def test_tracklist_double_click_plays(
    silent_flac_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two clicks on the same row within the double-click window play the track.

    Stubs `_play_current` so we don't actually open an audio device — on CI
    with no audio output, `play()` would fail, the track-failed callback
    sets `_end_pending`, and the next tick auto-advances to track 2 before
    the assertion fires (`_current_track_idx == 2` instead of 1).
    """
    from textual.widgets import ListItem

    from musickit.tui.app import BrowserList, MusickitApp, TrackList

    monkeypatch.setattr(MusickitApp, "_play_current", lambda self: None)

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
async def test_album_reentry_preserves_playing_state(silent_flac_template: Path, tmp_path: Path) -> None:
    """Regression: drill into an album, play a track, click the same album
    row in the browser again. The ▶ marker must keep showing on the
    actually-playing track — the user expects to land on the track
    that's still playing, not on row 0 with blank NowPlayingMeta.

    The marker derives from `AudioPlayer.current_source`, so the test
    simulates playback by setting that source directly.
    """
    from musickit.tui.app import BrowserList, MusickitApp, TrackList

    root = tmp_path / "lib"
    for n in range(1, 5):
        _make_track(
            root / "A" / "2020 - X", silent_flac_template, filename=f"{n:02d} - T{n}.m4a", title=f"T{n}", artist="A"
        )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        # Drill into the artist → album list shown in browser.
        artist = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(artist)  # type: ignore[attr-defined]
        await pilot.pause()
        album_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "album")
        album_obj = album_row.entry_data  # type: ignore[attr-defined]
        # Drill into the album → tracks shown in tracklist.
        pilot.app._handle_browser_selection(album_row)  # type: ignore[attr-defined]
        await pilot.pause()
        # Pretend track 2 is the playing one — pin the player's source
        # to that track's path so `_playing_track_idx_in` returns 1.
        pilot.app._player._current_source = album_obj.tracks[1].path  # type: ignore[attr-defined]
        # Re-click the same album row in the browser (browser still shows
        # the album list since we're at album-level navigation).
        album_row_again = next(
            c
            for c in browser.children
            if getattr(c, "entry_kind", None) == "album" and c.entry_data is album_obj  # type: ignore[attr-defined]
        )
        pilot.app._handle_browser_selection(album_row_again)  # type: ignore[attr-defined]
        await pilot.pause()
        # The ▶ marker is on track index 1 (derived from the player's source).
        assert pilot.app._current_track_idx == 1  # type: ignore[attr-defined]
        # Tracklist cursor lands on the playing track (visible row index 1), not row 0.
        tracklist = pilot.app.query_one(TrackList)
        assert tracklist.index == 1


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
        # Back at root: radio + mixes + the one artist.
        assert kinds == ["radio", "mixes", "artist"]


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


@pytest.mark.asyncio
async def test_fullscreen_toggle_restores_focus_to_tracklist(silent_flac_template: Path, tmp_path: Path) -> None:
    """Entering fullscreen and back must put focus back on the TrackList, not BrowserList."""
    from musickit.tui.app import MusickitApp
    from musickit.tui.widgets import TrackList

    root = tmp_path / "lib"
    album = root / "A" / "2020 - One"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", title="T", artist="A")

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        # Drill into the album so the TrackList is populated.
        from musickit.tui.app import BrowserList

        browser = pilot.app.query_one(BrowserList)
        artist_row = next(c for c in browser.children if getattr(c, "entry_data", None) == "A")
        pilot.app._handle_browser_selection(artist_row)  # type: ignore[attr-defined]
        await pilot.pause()
        album_row = browser.children[1]  # row 0 = back, row 1 = album
        pilot.app._handle_browser_selection(album_row)  # type: ignore[attr-defined]
        await pilot.pause()

        tracklist = pilot.app.query_one(TrackList)
        tracklist.focus()
        await pilot.pause()
        assert pilot.app.focused is tracklist

        # Toggle into fullscreen and back.
        app = pilot.app
        assert isinstance(app, MusickitApp)
        app.action_toggle_fullscreen()
        await pilot.pause()
        app.action_toggle_fullscreen()
        await pilot.pause()

        assert pilot.app.focused is tracklist


@pytest.mark.asyncio
async def test_fullscreen_actually_expands_visualizer_height(silent_flac_template: Path, tmp_path: Path) -> None:
    """`f` must defeat the base `max-height: 14` cap and grow the meter past 14 rows.

    Regression for the bug where `Screen.fullscreen Visualizer { max-height: 200; }`
    declared inside `Visualizer.DEFAULT_CSS` failed to override the base
    `max-height: 14` rule — the panel hid sidebar / tracklist correctly but
    the visualizer stayed capped at 14, leaving the bottom of the screen
    empty. Lifting the override to the app-level stylesheet (targeting
    `#visualizer` by id) is what makes the cascade actually win.
    """
    from musickit.tui.app import MusickitApp
    from musickit.tui.widgets import Visualizer

    root = tmp_path / "lib"
    _make_track(
        root / "A" / "2020 - One",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="A",
    )

    # 60-row terminal so the fullscreen viz has somewhere to grow into.
    async with MusickitApp(root).run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        app = pilot.app
        assert isinstance(app, MusickitApp)
        viz = app.query_one(Visualizer)

        # Pre-fullscreen: max-height honours the base 14-row cap.
        assert viz.size.height <= 14

        app.action_toggle_fullscreen()
        await pilot.pause()

        # Fullscreen: max-height must be lifted so the panel fills the column.
        # Effective ceiling is the new `max-height: 200` from app CSS; actual
        # height tracks the viewport (60 rows minus topbar/keybar/now-playing/
        # progress chrome ~ low 40s). Anything strictly above the old 14-cap
        # proves the override took effect.
        assert viz.size.height > 14, (
            f"fullscreen visualizer should be > 14 rows tall on a 60-row terminal; "
            f"got height={viz.size.height}, max_height={viz.styles.max_height}"
        )

        # Exit fullscreen — cap snaps back to 14.
        app.action_toggle_fullscreen()
        await pilot.pause()
        assert viz.size.height <= 14
