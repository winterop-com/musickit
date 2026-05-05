"""Visualizer pause / stop decay — bars fade to zero over ~1s instead of freezing.

When the audio engine stops publishing band levels (paused, stopped),
the shared-memory array used to stay frozen at the last decoded value,
which looked dead on long pauses. The UI tick now applies a per-frame
decay locally; on resume the pass-through from shared memory takes over
again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from musickit.tui.app import MusickitApp
from musickit.tui.widgets import Visualizer
from tests.test_library import _make_track


def test_decay_unit_is_strict_contraction() -> None:
    """The per-band decay rule is a strict contraction toward zero.

    The implementation lives at `_refresh_visualizer`'s else-branch as
    `[v * 0.858 if v > 0.005 else 0.0 for v in current]`. Verify the
    rule directly without booting the App — Pilot's 30 FPS timer makes
    "exactly one tick" hard to assert.
    """
    decay = lambda v: v * 0.858 if v > 0.005 else 0.0  # noqa: E731 — mirror the impl one-liner

    # Loud band shrinks but doesn't snap.
    assert 0 < decay(0.8) < 0.8
    # Below the floor → snaps to zero (avoids long no-op decay tail).
    assert decay(0.004) == 0.0
    # Repeated application reaches 0 in finite steps from a loud start.
    v = 0.99
    for _ in range(120):
        v = decay(v)
    assert v == 0.0


@pytest.mark.asyncio
async def test_visualizer_levels_drop_when_not_playing(silent_flac_template: Path, tmp_path: Path) -> None:
    """End-to-end: after seeding bars high and letting the app idle, levels reach zero."""
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    app = MusickitApp(root)
    async with app.run_test() as pilot:
        await pilot.pause()
        a = pilot.app
        assert isinstance(a, MusickitApp)
        viz = a.query_one(Visualizer)

        # Seed the bars high — simulates "just paused while audio was loud."
        viz.levels = [0.8] * len(viz.levels)

        # Idle for ~2s of wall time at 30 FPS = ~60 ticks. Our decay
        # constant takes ~30-60 ticks to reach the < 0.005 snap-to-zero
        # threshold from 0.8, so 2s is comfortably long.
        for _ in range(40):
            await pilot.pause(0.05)

        final = list(viz.levels)
        assert max(final) == 0.0, f"levels should have decayed to all-zero; got max={max(final)}"


# Silence pytest unused-import noise for fixtures used via direct invocation.
_ = pytest
