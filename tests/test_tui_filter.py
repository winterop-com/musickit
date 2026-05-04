"""Tests for the `/` filter on the focused pane (browser or tracklist)."""

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


def _three_artist_lib(silent_flac_template: Path, root: Path) -> None:
    for artist in ("Metallica", "Radiohead", "Imagine Dragons"):
        _make_track(
            root / artist / "2000 - Album",
            silent_flac_template,
            filename="01 - Track.m4a",
            title="Track",
            artist=artist,
        )


@pytest.mark.asyncio
async def test_slash_mounts_filter_input_when_browser_focused(silent_flac_template: Path, tmp_path: Path) -> None:
    """`/` while the browser is focused mounts a FilterInput targeting it."""
    from musickit.tui.app import MusickitApp
    from musickit.tui.widgets import FilterInput

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        inputs = list(pilot.app.query(FilterInput))
        assert len(inputs) == 1
        assert inputs[0].id == "filter-browser"
        assert pilot.app.focused is inputs[0]


@pytest.mark.asyncio
async def test_typing_narrows_browser_to_substring_matches(silent_flac_template: Path, tmp_path: Path) -> None:
    """Typing into the filter narrows the artist list to substring matches."""
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("r", "a")
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        names = [getattr(c, "entry_data", None) for c in browser.children if getattr(c, "entry_kind", None) == "artist"]
        # "ra" matches "Radiohead" and "Imagine Dragons" (drag**ra**) — wait, no:
        # "ra" appears in "Radiohead" and in "Imagine D**ra**gons".
        assert sorted(filter(None, names)) == sorted(["Radiohead", "Imagine Dragons"])
        # Radio entry is hidden while filter is active.
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        assert "radio" not in kinds


@pytest.mark.asyncio
async def test_escape_dismisses_filter_and_restores_full_list(silent_flac_template: Path, tmp_path: Path) -> None:
    """Esc: input gone, full list back, focus on browser."""
    from musickit.tui.app import BrowserList, MusickitApp
    from musickit.tui.widgets import FilterInput

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("r", "a")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not list(pilot.app.query(FilterInput))
        browser = pilot.app.query_one(BrowserList)
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # Radio + 3 artists, in either order (radio is pinned first).
        assert kinds.count("artist") == 3
        assert kinds[0] == "radio"
        assert isinstance(pilot.app.focused, BrowserList)


@pytest.mark.asyncio
async def test_enter_dismisses_input_keeps_filter_active(silent_flac_template: Path, tmp_path: Path) -> None:
    """Enter on the filter input dismisses the input but keeps the narrowed list."""
    from musickit.tui.app import BrowserList, MusickitApp
    from musickit.tui.widgets import FilterInput

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("m", "e", "t")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Input is gone, filter list stays narrowed.
        assert not list(pilot.app.query(FilterInput))
        browser = pilot.app.query_one(BrowserList)
        names = [getattr(c, "entry_data", None) for c in browser.children if getattr(c, "entry_kind", None) == "artist"]
        assert names == ["Metallica"]


@pytest.mark.asyncio
async def test_drilling_into_artist_clears_browser_filter(silent_flac_template: Path, tmp_path: Path) -> None:
    """Selecting an artist auto-clears any active browser filter."""
    from musickit.tui.app import BrowserList, MusickitApp
    from musickit.tui.widgets import FilterInput

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("r", "a", "d")
        await pilot.pause()
        # Pick the Radiohead row directly (avoids the input gobbling Enter).
        browser = pilot.app.query_one(BrowserList)
        radiohead_row = next(c for c in browser.children if getattr(c, "entry_data", None) == "Radiohead")
        pilot.app._handle_browser_selection(radiohead_row)  # type: ignore[attr-defined]
        await pilot.pause()
        # Filter input should be gone, filter string cleared.
        assert not list(pilot.app.query(FilterInput))
        assert pilot.app._browser_filter == ""  # type: ignore[attr-defined]
        # Browser is now showing albums for Radiohead.
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        assert "album" in kinds


@pytest.mark.asyncio
async def test_no_match_shows_placeholder_row(silent_flac_template: Path, tmp_path: Path) -> None:
    """Filter with no hits shows a `(no matches)` placeholder."""
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    _three_artist_lib(silent_flac_template, root)

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        # Type something that matches nothing.
        await pilot.press("z", "z", "z", "z")
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        # No artist rows. One row exists (the placeholder Static).
        assert "artist" not in kinds
        assert len(browser.children) >= 1


@pytest.mark.asyncio
async def test_filter_works_on_tracklist(silent_flac_template: Path, tmp_path: Path) -> None:
    """`/` while the tracklist is focused narrows tracks by title."""
    from musickit.tui.app import BrowserList, MusickitApp, TrackList
    from musickit.tui.widgets import FilterInput

    root = tmp_path / "lib"
    album_dir = root / "Radiohead" / "2007 - In Rainbows"
    for n, title in enumerate(["15 Step", "Bodysnatchers", "Nude", "Reckoner"], start=1):
        _make_track(
            album_dir,
            silent_flac_template,
            filename=f"{n:02d} - {title}.m4a",
            title=title,
            artist="Radiohead",
        )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        # Drill into Radiohead → In Rainbows so the tracklist is populated.
        browser = pilot.app.query_one(BrowserList)
        artist_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(artist_row)  # type: ignore[attr-defined]
        await pilot.pause()
        album_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "album")
        pilot.app._handle_browser_selection(album_row)  # type: ignore[attr-defined]
        await pilot.pause()
        tracklist = pilot.app.query_one(TrackList)
        tracklist.focus()
        await pilot.pause()
        # Now filter the tracklist.
        await pilot.press("slash")
        await pilot.pause()
        inputs = list(pilot.app.query(FilterInput))
        assert len(inputs) == 1
        assert inputs[0].id == "filter-tracklist"
        await pilot.press("n", "u")
        await pilot.pause()
        # Visible track-row count reflects the filter (only "Nude" matches "nu").
        track_rows = [c for c in tracklist.children if getattr(c, "track_index", None) is not None]
        assert len(track_rows) == 1
        assert track_rows[0].track_index == 2  # type: ignore[attr-defined]  # zero-indexed: Nude is 3rd track
