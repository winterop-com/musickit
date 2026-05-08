"""Tag editor — model extension + screen save path."""

from __future__ import annotations

import shutil
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
    tags["trkn"] = [(1, 5)]
    tags["disk"] = [(1, 2)]
    tags["\xa9day"] = ["2007"]
    mp4.save()
    return dst


def test_tag_overrides_track_no_preserves_total(silent_flac_template: Path, tmp_path: Path) -> None:
    """Setting only `track_no` keeps the existing track total intact."""
    from musickit.metadata import TagOverrides, apply_tag_overrides

    track = _make_track(tmp_path / "Album", silent_flac_template, filename="01.m4a", title="T", artist="A")
    apply_tag_overrides(track, TagOverrides(track_no=3))

    mp4 = MP4(track)
    trkn = mp4.tags["trkn"][0]  # type: ignore[index]
    assert trkn == (3, 5), "track_no override must keep total=5"


def test_tag_overrides_disc_no_preserves_total(silent_flac_template: Path, tmp_path: Path) -> None:
    from musickit.metadata import TagOverrides, apply_tag_overrides

    track = _make_track(tmp_path / "Album", silent_flac_template, filename="01.m4a", title="T", artist="A")
    apply_tag_overrides(track, TagOverrides(disc_no=2))

    mp4 = MP4(track)
    disk = mp4.tags["disk"][0]  # type: ignore[index]
    assert disk == (2, 2)


def test_tag_overrides_both_no_and_total(silent_flac_template: Path, tmp_path: Path) -> None:
    from musickit.metadata import TagOverrides, apply_tag_overrides

    track = _make_track(tmp_path / "Album", silent_flac_template, filename="01.m4a", title="T", artist="A")
    apply_tag_overrides(track, TagOverrides(track_no=4, track_total=11))

    mp4 = MP4(track)
    trkn = mp4.tags["trkn"][0]  # type: ignore[index]
    assert trkn == (4, 11)


def test_tag_overrides_flac_track_no(silent_flac_template: Path, tmp_path: Path) -> None:
    """FLAC: TRACKNUMBER updates while TRACKTOTAL stays untouched."""
    from mutagen.flac import FLAC

    from musickit.metadata import TagOverrides, apply_tag_overrides

    src = tmp_path / "track.flac"
    shutil.copy2(silent_flac_template, src)
    flac = FLAC(src)
    flac["TRACKNUMBER"] = ["1"]
    flac["TRACKTOTAL"] = ["10"]
    flac.save()

    apply_tag_overrides(src, TagOverrides(track_no=7))
    flac = FLAC(src)
    assert flac["TRACKNUMBER"] == ["7"]
    assert flac["TRACKTOTAL"] == ["10"], "track_no override must not clobber TRACKTOTAL"


@pytest.mark.asyncio
async def test_track_editor_saves_changed_fields(silent_flac_template: Path, tmp_path: Path) -> None:
    """Open editor, edit Title, Ctrl+S → tag is written + LibraryTrack patched."""
    from textual.widgets import Input

    from musickit.tui.app import MusickitApp
    from musickit.tui.tag_editor import TrackTagEditorScreen

    root = tmp_path / "lib"
    track_path = _make_track(
        root / "Beck" / "2002 - Sea Change",
        silent_flac_template,
        filename="01 - Lost Cause.m4a",
        title="Lost Cause",
        artist="Beck",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        # Drill into Beck → Sea Change so the tracklist is populated.
        from musickit.tui.app import BrowserList

        browser = pilot.app.query_one(BrowserList)
        artist_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(artist_row)  # type: ignore[attr-defined]
        await pilot.pause()
        album_row = next(c for c in browser.children if getattr(c, "entry_kind", None) == "album")
        pilot.app._handle_browser_selection(album_row)  # type: ignore[attr-defined]
        await pilot.pause()

        # Push the editor manually so we can manipulate inputs reliably.
        track = pilot.app._current_album.tracks[0]  # type: ignore[attr-defined]
        screen = TrackTagEditorScreen(pilot.app, track)  # type: ignore[arg-type]
        pilot.app.push_screen(screen)
        await pilot.pause()

        title_input = screen.query_one("#f-title", Input)
        title_input.value = "Lost Cause (Edited)"
        screen.action_save()
        await pilot.pause()

    # File on disk should reflect the new title.
    mp4 = MP4(track_path)
    assert str(mp4.tags["\xa9nam"][0]) == "Lost Cause (Edited)"  # type: ignore[index]


@pytest.mark.asyncio
async def test_track_editor_year_validation(silent_flac_template: Path, tmp_path: Path) -> None:
    """A non-4-digit year keeps the editor open and does NOT touch the file."""
    from textual.widgets import Input

    from musickit.tui.app import MusickitApp
    from musickit.tui.tag_editor import TrackTagEditorScreen

    root = tmp_path / "lib"
    track_path = _make_track(
        root / "X" / "2000 - A",
        silent_flac_template,
        filename="01 - T.m4a",
        title="T",
        artist="X",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        from musickit.tui.app import BrowserList
        from tests._tui_wait import wait_for_browser_child

        browser = pilot.app.query_one(BrowserList)
        a = await wait_for_browser_child(
            pilot,
            lambda: browser.children,
            lambda c: getattr(c, "entry_kind", None) == "artist",
            description="artist row",
        )
        pilot.app._handle_browser_selection(a)  # type: ignore[attr-defined]
        await pilot.pause()
        b = await wait_for_browser_child(
            pilot,
            lambda: browser.children,
            lambda c: getattr(c, "entry_kind", None) == "album",
            description="album row",
        )
        pilot.app._handle_browser_selection(b)  # type: ignore[attr-defined]
        await pilot.pause()

        track = pilot.app._current_album.tracks[0]  # type: ignore[attr-defined]
        screen = TrackTagEditorScreen(pilot.app, track)  # type: ignore[arg-type]
        pilot.app.push_screen(screen)
        await pilot.pause()

        screen.query_one("#f-year", Input).value = "07"  # not 4 digits
        screen.action_save()
        await pilot.pause()
        # Editor stays open on validation failure (didn't dismiss).
        assert pilot.app.screen is screen, "editor should stay open on validation failure"

    # File on disk wasn't touched (year still the original 2007 from the helper).
    mp4 = MP4(track_path)
    assert str(mp4.tags["\xa9day"][0]) == "2007"  # type: ignore[index]


@pytest.mark.asyncio
async def test_e_opens_album_editor_from_browser_before_playback(silent_flac_template: Path, tmp_path: Path) -> None:
    """Regression: `e` used to silently no-op until a song was playing.
    Now it opens the album editor based on the focused row, regardless of
    whether anything is playing.
    """
    from musickit.tui.app import BrowserList, MusickitApp
    from musickit.tui.tag_editor import AlbumTagEditorScreen

    root = tmp_path / "lib"
    _make_track(
        root / "Beck" / "2002 - Sea Change",
        silent_flac_template,
        filename="01 - Lost Cause.m4a",
        title="Lost Cause",
        artist="Beck",
    )

    async with MusickitApp(root).run_test() as pilot:
        await pilot.pause()
        # Wait for the library scan + browser populate. A single pause()
        # is enough on macOS but flakes on Linux CI where scan timing
        # differs; poll for an artist row instead.
        browser = pilot.app.query_one(BrowserList)
        for _ in range(50):
            if any(getattr(c, "entry_kind", None) == "artist" for c in browser.children):
                break
            await pilot.pause(0.05)
        # Drill into the artist so the browser shows an album row.
        a = next(c for c in browser.children if getattr(c, "entry_kind", None) == "artist")
        pilot.app._handle_browser_selection(a)  # type: ignore[attr-defined]
        await pilot.pause()
        # Highlight the album row + focus the browser.
        browser.focus()
        for _ in range(50):
            if any(getattr(c, "entry_kind", None) == "album" for c in browser.children):
                break
            await pilot.pause(0.05)
        album_idx = next(i for i, c in enumerate(browser.children) if getattr(c, "entry_kind", None) == "album")
        browser.index = album_idx
        await pilot.pause()
        # `e` while NO playback is in progress.
        assert pilot.app._current_track_idx is None  # type: ignore[attr-defined]
        await pilot.press("e")
        await pilot.pause()
        # The album editor should be on top of the screen stack.
        assert any(isinstance(s, AlbumTagEditorScreen) for s in pilot.app.screen_stack)


@pytest.mark.asyncio
async def test_e_keybinding_does_nothing_in_subsonic_mode(tmp_path: Path) -> None:
    """`e` is a no-op when Subsonic-client mode is active (synthetic paths)."""
    from unittest.mock import MagicMock

    from musickit.tui.app import MusickitApp
    from musickit.tui.tag_editor import TrackTagEditorScreen

    fake_client = MagicMock()
    fake_client.close = MagicMock()
    async with MusickitApp(None, subsonic_client=fake_client).run_test() as pilot:
        await pilot.pause()
        # No editor screen pushed.
        await pilot.press("e")
        await pilot.pause()
        # Walk the screen stack: no TrackTagEditorScreen at any depth.
        for screen in pilot.app.screen_stack:
            assert not isinstance(screen, TrackTagEditorScreen)
