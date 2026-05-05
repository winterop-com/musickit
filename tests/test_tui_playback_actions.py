"""TUI playback action coverage — drive the bound actions via Pilot.

The actions exercised here:
  - toggle_pause / next_track / prev_track
  - seek_fwd / seek_back
  - vol_up / vol_down (mpv-style `9` / `0`, plus the legacy `+` / `-`)
  - cycle_repeat / toggle_shuffle

Each test boots a fresh `MusickitApp` against a tiny fixture library,
fires the relevant action via `App.action_*` (matches what the binding
would dispatch), and asserts the observable state — `_player.volume`,
`_player.is_paused`, `_repeat`, `_shuffle`, etc.

We don't go through `pilot.press("...")` for most of these because
keypress dispatch racing with library-scan settling makes the tests
flaky on slow machines. Calling `app.action_*` directly tests the same
behavior with no race surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from musickit.tui.app import MusickitApp
from musickit.tui.types import RepeatMode
from tests.test_library import _make_track


async def _bring_up_app(tmp_path: Path, silent_flac_template: Path) -> MusickitApp:
    """Build a 2-track album and yield a running `MusickitApp` pilot."""
    root = tmp_path / "lib"
    album = root / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(album, silent_flac_template, filename="01 - Radioactive.m4a", title="Radioactive")
    _make_track(album, silent_flac_template, filename="02 - Tiptoe.m4a", title="Tiptoe", track_no=2)
    return MusickitApp(root)


def _app(pilot_app: object) -> MusickitApp:
    """Narrow `pilot.app` to `MusickitApp` for static type checkers."""
    assert isinstance(pilot_app, MusickitApp)
    return pilot_app


def _repeat_of(a: MusickitApp) -> RepeatMode:
    """Read `_repeat` through a function so mypy doesn't literal-narrow it."""
    return a._repeat


@pytest.mark.asyncio
async def test_action_vol_up_clamps_at_100(silent_flac_template: Path, tmp_path: Path) -> None:
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        before = a._player.volume
        a.action_vol_up()
        await pilot.pause()
        assert a._player.volume == 100
        assert a._player.volume >= before


@pytest.mark.asyncio
async def test_action_vol_down_steps_by_5(silent_flac_template: Path, tmp_path: Path) -> None:
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        a.action_vol_down()
        await pilot.pause()
        assert a._player.volume == 95
        a.action_vol_down()
        await pilot.pause()
        assert a._player.volume == 90


@pytest.mark.asyncio
async def test_action_toggle_pause_flips_state(silent_flac_template: Path, tmp_path: Path) -> None:
    """`toggle_pause` sends Op.TOGGLE_PAUSE to the audio subprocess; the
    engine processes commands on a 100ms poll, so we wait up to ~1s for
    the shared-memory `paused` flag to flip rather than racing with it.
    """
    import time

    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        before = a._player.is_paused
        a.action_toggle_pause()
        deadline = time.time() + 2.0
        while time.time() < deadline and a._player.is_paused == before:
            await pilot.pause(0.05)
        assert a._player.is_paused != before


@pytest.mark.asyncio
async def test_action_toggle_shuffle_flips_state(silent_flac_template: Path, tmp_path: Path) -> None:
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        assert a._shuffle is False
        a.action_toggle_shuffle()
        assert a._shuffle is True
        a.action_toggle_shuffle()
        assert a._shuffle is False


@pytest.mark.asyncio
async def test_action_cycle_repeat_walks_three_states(silent_flac_template: Path, tmp_path: Path) -> None:
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        assert _repeat_of(a) == RepeatMode.OFF
        a.action_cycle_repeat()
        assert _repeat_of(a) == RepeatMode.ALBUM
        a.action_cycle_repeat()
        assert _repeat_of(a) == RepeatMode.TRACK
        a.action_cycle_repeat()
        assert _repeat_of(a) == RepeatMode.OFF


@pytest.mark.asyncio
async def test_action_seek_fwd_back_no_crash_when_not_playing(silent_flac_template: Path, tmp_path: Path) -> None:
    """Seek with nothing playing is a no-op — must not raise."""
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        # Both actions read self._player.position (defaults to 0) and call
        # seek(); seek() returns early when duration <= 0.
        a.action_seek_fwd()
        a.action_seek_back()
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_prev_track_with_no_album_is_safe(silent_flac_template: Path, tmp_path: Path) -> None:
    """`prev_track` with no album drilled in must short-circuit cleanly."""
    app = await _bring_up_app(tmp_path, silent_flac_template)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = _app(pilot.app)
        # No album opened → action returns early, no crash.
        a.action_prev_track()
        await pilot.pause()


# Silence pytest unused-import noise for fixtures used via direct invocation.
_ = pytest
