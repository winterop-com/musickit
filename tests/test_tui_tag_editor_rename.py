"""AlbumTagEditorScreen: folder rename triggers when album/artist/year changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input

from musickit.tui.app import BrowserList, MusickitApp
from musickit.tui.tag_editor import AlbumTagEditorScreen
from tests._tui_wait import wait_for_browser_child
from tests.test_library import _make_track


async def _drill_into_album(pilot: object, root: Path) -> None:
    app = pilot.app  # type: ignore[attr-defined]
    browser = app.query_one(BrowserList)
    artist_row = await wait_for_browser_child(
        pilot,
        lambda: browser.children,
        lambda c: getattr(c, "entry_kind", None) == "artist",
        description="artist row",
    )
    app._handle_browser_selection(artist_row)


async def _open_album_editor(pilot: object) -> AlbumTagEditorScreen:
    app = pilot.app  # type: ignore[attr-defined]
    browser = app.query_one(BrowserList)
    album_row = await wait_for_browser_child(
        pilot,
        lambda: browser.children,
        lambda c: getattr(c, "entry_kind", None) == "album",
        description="album row",
    )
    album = album_row.entry_data
    screen = AlbumTagEditorScreen(app, album)
    app.push_screen(screen)
    await pilot.pause()  # type: ignore[attr-defined]
    return screen


@pytest.mark.asyncio
async def test_album_rename_when_album_title_changes(silent_flac_template: Path, tmp_path: Path) -> None:
    """Editing the album title renames the on-disk folder to match `YYYY - <new title>`."""
    root = tmp_path / "lib"
    _make_track(
        root / "Artist" / "2020 - Old Title",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="Artist",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, root)
        await pilot.pause()
        screen = await _open_album_editor(pilot)

        screen.query_one("#f-album", Input).value = "New Title"
        screen.action_save()
        await pilot.pause()

    # Filesystem reflects the rename.
    assert not (root / "Artist" / "2020 - Old Title").exists(), "old folder should be gone"
    assert (root / "Artist" / "2020 - New Title").exists(), "new folder should exist"
    # Track file moved with it.
    assert (root / "Artist" / "2020 - New Title" / "01 - T.m4a").exists()


@pytest.mark.asyncio
async def test_album_rename_across_artists(silent_flac_template: Path, tmp_path: Path) -> None:
    """Changing tag_album_artist moves the album under a new artist parent dir."""
    root = tmp_path / "lib"
    _make_track(
        root / "OldArtist" / "2020 - Album",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="OldArtist",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, root)
        await pilot.pause()
        screen = await _open_album_editor(pilot)

        screen.query_one("#f-album_artist", Input).value = "NewArtist"
        screen.action_save()
        await pilot.pause()

    assert not (root / "OldArtist" / "2020 - Album").exists()
    assert (root / "NewArtist" / "2020 - Album" / "01 - T.m4a").exists()


@pytest.mark.asyncio
async def test_album_no_rename_when_only_genre_changes(silent_flac_template: Path, tmp_path: Path) -> None:
    """Genre-only edits don't move the folder — only album/artist/year affect the path."""
    root = tmp_path / "lib"
    _make_track(
        root / "Artist" / "2020 - Album",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="Artist",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, root)
        await pilot.pause()
        screen = await _open_album_editor(pilot)

        screen.query_one("#f-genre", Input).value = "Indie Rock"
        screen.action_save()
        await pilot.pause()

    # Folder unchanged.
    assert (root / "Artist" / "2020 - Album" / "01 - T.m4a").exists()


@pytest.mark.asyncio
async def test_album_rename_collision_keeps_modal_open(silent_flac_template: Path, tmp_path: Path) -> None:
    """Renaming into an existing folder fails gracefully: modal stays open with a warning."""
    root = tmp_path / "lib"
    _make_track(
        root / "Artist" / "2020 - Album A",
        silent_flac_template,
        filename="01 - A.m4a",
        title="A",
        artist="Artist",
    )
    # Pre-create the collision target.
    _make_track(
        root / "Artist" / "2020 - Album B",
        silent_flac_template,
        filename="01 - B.m4a",
        title="B",
        artist="Artist",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        await _drill_into_album(pilot, root)
        await pilot.pause()
        screen = await _open_album_editor(pilot)

        screen.query_one("#f-album", Input).value = "Album B"
        screen.action_save()
        await pilot.pause()

        assert pilot.app.screen is screen, "modal should stay open on rename collision"

    # Both folders still exist (rename was refused).
    assert (root / "Artist" / "2020 - Album A").exists()
    assert (root / "Artist" / "2020 - Album B").exists()
