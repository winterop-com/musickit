"""Pure next-track decision logic — shuffle / repeat / stop."""

from __future__ import annotations

import random

from musickit.tui.types import RepeatMode


def compute_next_track_idx(
    *,
    current_idx: int,
    track_count: int,
    shuffle: bool,
    repeat: RepeatMode,
    force: bool,
) -> int | None:
    """Decide the next track index based on shuffle/repeat state.

    Returns the next index, the same index when `repeat=TRACK` (caller replays),
    or `None` when playback should stop. `force=True` overrides `RepeatMode.TRACK`
    to act as if repeat were OFF — used by the explicit "Next" action so the
    user can skip out of a track-loop.
    """
    if not force and repeat is RepeatMode.TRACK:
        return current_idx
    if shuffle:
        if track_count <= 1:
            return None
        choices = [i for i in range(track_count) if i != current_idx]
        return random.choice(choices)
    next_idx = current_idx + 1
    if next_idx < track_count:
        return next_idx
    if repeat is RepeatMode.ALBUM:
        return 0
    return None
