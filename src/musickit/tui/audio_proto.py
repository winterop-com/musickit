"""Wire protocol for the audio subprocess — Command / Event types + shared state.

Imported by both the UI (`AudioPlayer`) and the audio subprocess
(`audio_engine`). All types are simple dataclasses so they pickle
cleanly across the `multiprocessing.Queue` boundary.

The high-frequency state (position, band levels, volume) lives in
shared memory (`multiprocessing.Value` / `Array`) instead of going
through the queues — those reads/writes happen at audio-callback rate
and would dominate the IPC budget if they round-tripped per frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Op(str, Enum):
    """Command opcodes — UI → engine."""

    PLAY = "play"
    STOP = "stop"
    TOGGLE_PAUSE = "toggle_pause"
    SEEK = "seek"
    SET_REPLAYGAIN_MODE = "set_replaygain_mode"
    SHUTDOWN = "shutdown"


class EventOp(str, Enum):
    """Event opcodes — engine → UI."""

    STARTED = "started"
    TRACK_END = "track_end"
    TRACK_FAILED = "track_failed"
    METADATA_CHANGED = "metadata_changed"


@dataclass
class Command:
    """One UI → engine command on the cmd_queue."""

    op: Op
    payload: Any = None
    # Generation counter — bumped on every PLAY so the engine can drop
    # stale openers when the user switches sources faster than av.open().
    gen: int = 0


@dataclass
class Event:
    """One engine → UI event on the event_queue."""

    op: EventOp
    payload: Any = None


# ---------------------------------------------------------------------------
# PLAY payload
# ---------------------------------------------------------------------------


@dataclass
class PlayPayload:
    """Args for `Op.PLAY` — source + per-track ReplayGain tags."""

    # Path or URL. Pickling Path is fine; we serialise to str on the wire
    # so the engine doesn't import musickit-specific types here.
    source: str
    is_path: bool
    replaygain: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# STARTED payload
# ---------------------------------------------------------------------------


@dataclass
class StartedPayload:
    """Engine → UI right after a successful PLAY: duration + liveness."""

    source: str
    is_path: bool
    duration_s: float
    is_live: bool
    stream_title: str | None
    stream_station_name: str | None
    sample_rate: int


# ---------------------------------------------------------------------------
# Indices into the shared `band_levels` Array (24 floats, 0.0–1.0).
# Defined here so both sides agree on the layout.
# ---------------------------------------------------------------------------

VIS_BANDS = 24
"""Number of FFT bins surfaced to the UI for the spectrum visualizer."""

SAMPLE_RATE = 44100
"""Output sample rate the engine resamples everything to."""

CHANNELS = 2
"""Stereo only."""
