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
async def test_app_populates_tree_from_index(silent_flac_template: Path, tmp_path: Path) -> None:
    """App.run_test() boots, scans the library, populates the artist tree."""
    from musickit.tui.app import LibraryTree, MusickitApp

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
        # Force one event loop pass so on_mount has run.
        await pilot.pause()
        tree = pilot.app.query_one(LibraryTree)
        artist_labels = {str(node.label) for node in tree.root.children}
        assert artist_labels == {"Imagine Dragons", "Linkin Park"}


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
    """No albums under the root → tree shows the empty placeholder, no crash."""
    from musickit.tui.app import LibraryTree, MusickitApp

    root = tmp_path / "lib"
    root.mkdir()

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        tree = pilot.app.query_one(LibraryTree)
        labels = [str(c.label) for c in tree.root.children]
        assert labels == ["(no albums)"]
