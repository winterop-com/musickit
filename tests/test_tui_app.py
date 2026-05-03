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
        assert kinds == ["artist", "artist"]
        names = sorted(str(getattr(c, "entry_data", "")) for c in browser.children)
        assert names == ["Imagine Dragons", "Linkin Park"]


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
    """No albums under the root → browser shows the empty placeholder, no crash."""
    from musickit.tui.app import BrowserList, MusickitApp

    root = tmp_path / "lib"
    root.mkdir()

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        browser = pilot.app.query_one(BrowserList)
        # Single placeholder ListItem.
        assert len(browser.children) == 1


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
        # Drill into the only artist.
        pilot.app._handle_browser_selection(browser.children[0])  # type: ignore[attr-defined]
        await pilot.pause()
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        assert kinds[0] == "up"
        assert kinds[1:] == ["album", "album"]
        # Pop back via the `..` row.
        pilot.app._handle_browser_selection(browser.children[0])  # type: ignore[attr-defined]
        await pilot.pause()
        kinds = [getattr(c, "entry_kind", None) for c in browser.children]
        assert kinds == ["artist"]
