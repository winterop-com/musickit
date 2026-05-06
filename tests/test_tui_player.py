"""AudioEngine: PyAV decoder thread + sounddevice callback (stubbed).

`AudioPlayer` itself is now a thin RPC client that spawns a separate
process. The audio logic — decoder loop, callback, pause/seek/track-end
— lives in `AudioEngine`. These tests drive the engine in-process so
sounddevice can be monkey-patched, observing events on the queue.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from musickit.tui.audio_engine import AudioEngine, SharedState
from musickit.tui.audio_proto import (
    SAMPLE_RATE,
    VIS_BANDS,
    Command,
    Event,
    EventOp,
    Op,
    PlayPayload,
)

# `silent_m4a` is provided session-scoped via tests/conftest.py so a single
# convert-to-alac call serves every audio test in the run; PyAV segfaulted
# when multiple test files re-converted the same template in one session.


# ---------------------------------------------------------------------------
# Engine harness — drives `AudioEngine` in-process with a fake output stream.
# ---------------------------------------------------------------------------


class _FakeOutputStream:
    """Stand-in for `sounddevice.OutputStream` that drives the callback in a thread."""

    def __init__(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: Callable[..., None],
        blocksize: int,
        latency: str | float | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.latency = latency
        self.dtype = dtype
        self.callback = callback
        self.blocksize = blocksize
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def close(self) -> None:
        self.stop()

    def _loop(self) -> None:
        outdata = np.zeros((self.blocksize, self.channels), dtype=np.float32)
        while not self._stop.is_set():
            self.callback(outdata, self.blocksize, None, 0)
            # Drive the callback faster than realtime so tests don't need to wait
            # an entire track length for end-of-stream.
            time.sleep(0.001)


@pytest.fixture
def fake_stream(monkeypatch: pytest.MonkeyPatch) -> type[_FakeOutputStream]:
    """Replace `sounddevice.OutputStream` with a thread-driven fake."""
    import sounddevice as sd  # type: ignore[import-untyped]

    monkeypatch.setattr(sd, "OutputStream", _FakeOutputStream)
    return _FakeOutputStream


def _make_engine() -> tuple[AudioEngine, SharedState, queue.Queue[Event]]:
    """Build a SharedState, event queue, and AudioEngine for in-process testing.

    Uses real `multiprocessing.Value`/`Array` so the engine's lock /
    `.value` / `[i]` calls all work just like in the subprocess. The
    event queue is a `queue.Queue` (thread-safe, no pickling) — fine for
    in-process tests since `MagicMock`-style assertions on payloads work
    without serialization.
    """
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    state = SharedState(
        position_frames=ctx.Value("q", 0),
        duration_s=ctx.Value("d", 0.0),
        paused=ctx.Value("b", 0),
        stopped=ctx.Value("b", 1),
        is_live=ctx.Value("b", 0),
        volume=ctx.Value("d", 1.0),
        replaygain_multiplier=ctx.Value("d", 1.0),
        band_levels=ctx.Array("d", [0.0] * VIS_BANDS),
    )
    events: queue.Queue[Event] = queue.Queue()
    engine = AudioEngine(events, state)  # type: ignore[arg-type]
    return engine, state, events


def _drain_until(events: queue.Queue[Event], op: EventOp, timeout: float = 3.0) -> Event | None:
    """Pop events until one with `op` arrives or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            event = events.get(timeout=0.05)
        except queue.Empty:
            continue
        if event.op is op:
            return event
    return None


def _play(engine: AudioEngine, source: Path | str, gen: int = 1) -> None:
    engine.dispatch(
        Command(
            op=Op.PLAY,
            payload=PlayPayload(source=str(source), is_path=isinstance(source, Path)),
            gen=gen,
        )
    )


# ---------------------------------------------------------------------------
# Decoder + callback smoke tests
# ---------------------------------------------------------------------------


def test_engine_decodes_silent_m4a(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Open a real m4a; the decoder thread + callback hand off bytes."""
    engine, state, _events = _make_engine()
    try:
        _play(engine, silent_m4a)
        # Wait until the engine has consumed something.
        deadline = time.time() + 5.0
        while time.time() < deadline and state.position_frames.value == 0:
            time.sleep(0.01)
        assert state.position_frames.value > 0
    finally:
        engine.teardown()


def test_engine_emits_track_end(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Track-end fires once via the event queue when decode finishes."""
    engine, _state, events = _make_engine()
    try:
        _play(engine, silent_m4a)
        end_event = _drain_until(events, EventOp.TRACK_END, timeout=10.0)
        assert end_event is not None, "TRACK_END never fired"
    finally:
        engine.teardown()


def test_engine_pause_writes_silence(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Pausing freezes the position counter — callback writes silence."""
    engine, state, _events = _make_engine()
    try:
        _play(engine, silent_m4a)
        # Wait for some progress.
        deadline = time.time() + 3.0
        while time.time() < deadline and state.position_frames.value < SAMPLE_RATE // 100:
            time.sleep(0.01)
        engine.dispatch(Command(op=Op.TOGGLE_PAUSE))
        # The "value before paused" can't be sampled atomically with the
        # toggle, so settle a moment then sample twice.
        time.sleep(0.05)
        before = state.position_frames.value
        time.sleep(0.1)
        after = state.position_frames.value
        assert after == before, "position must not advance while paused"
    finally:
        engine.teardown()


# ---------------------------------------------------------------------------
# ReplayGain (pure logic; doesn't touch the engine)
# ---------------------------------------------------------------------------


def test_replaygain_off_returns_unity_multiplier() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    assert compute_replaygain_multiplier({}, "off") == 1.0
    assert compute_replaygain_multiplier({"replaygain_track_gain": "-6.0 dB"}, "off") == 1.0


def test_replaygain_track_mode_uses_track_gain() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    multiplier = compute_replaygain_multiplier({"replaygain_track_gain": "-6.0 dB"}, "track")
    assert abs(multiplier - 0.501) < 0.01


def test_replaygain_album_mode_uses_album_gain() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    multiplier = compute_replaygain_multiplier(
        {"replaygain_album_gain": "-12.0 dB", "replaygain_track_gain": "-6.0 dB"},
        "album",
    )
    assert abs(multiplier - 0.251) < 0.01


def test_replaygain_auto_prefers_album_falls_back_to_track() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    both = compute_replaygain_multiplier(
        {"replaygain_album_gain": "-12.0 dB", "replaygain_track_gain": "-6.0 dB"},
        "auto",
    )
    track_only = compute_replaygain_multiplier({"replaygain_track_gain": "-6.0 dB"}, "auto")
    assert abs(both - 0.251) < 0.01  # uses album
    assert abs(track_only - 0.501) < 0.01  # falls back to track


def test_replaygain_peak_clamp_prevents_clipping() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    # +12 dB gain (multiplier ~4) but peak=0.8 → clamp to 1/0.8 = 1.25.
    multiplier = compute_replaygain_multiplier(
        {"replaygain_track_gain": "+12.0 dB", "replaygain_track_peak": "0.8"},
        "track",
    )
    assert abs(multiplier - 1.25) < 0.01


def test_replaygain_handles_various_tag_formats() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    # `-6.34 dB`, `-6.34 db`, `-6.34` (no unit) — all decode as -6.34 dB.
    expected = 10 ** (-6.34 / 20)
    for value in ["-6.34 dB", "-6.34 db", "-6.34"]:
        m = compute_replaygain_multiplier({"replaygain_track_gain": value}, "track")
        assert abs(m - expected) < 0.01


def test_replaygain_no_tags_returns_unity() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    assert compute_replaygain_multiplier({"some_other_tag": "1.0"}, "track") == 1.0


def test_replaygain_garbage_tag_returns_unity() -> None:
    from musickit.tui.player import compute_replaygain_multiplier

    assert compute_replaygain_multiplier({"replaygain_track_gain": "not a number"}, "track") == 1.0


# ---------------------------------------------------------------------------
# Engine command-dispatch surface
# ---------------------------------------------------------------------------


def test_engine_handles_unopenable_file_softly(tmp_path: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """A corrupt / non-audio file emits TRACK_FAILED, doesn't crash the engine."""
    engine, _state, events = _make_engine()
    try:
        bogus = tmp_path / "not-audio.m4a"
        bogus.write_bytes(b"\x00" * 100)
        _play(engine, bogus)
        failed = _drain_until(events, EventOp.TRACK_FAILED, timeout=5.0)
        assert failed is not None
        assert str(bogus) in failed.payload["source"]
    finally:
        engine.teardown()


def test_engine_stop_is_safe_when_never_played(fake_stream: type[_FakeOutputStream]) -> None:
    engine, _state, _events = _make_engine()
    try:
        engine.dispatch(Command(op=Op.STOP))  # must not raise
    finally:
        engine.teardown()


def test_stale_decoder_does_not_corrupt_next_playback(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """A slow-to-exit decoder must write to its own queue, not the next track's.

    Regression: previously the decoder thread read self._queue + self._stopped
    by reference. After teardown reset those to point at a new playback's
    state, the stale decoder could push old PCM (or its end-of-stream
    sentinel) into the new playback's queue, corrupting the next track.
    """
    engine, _state, _events = _make_engine()
    try:
        _play(engine, silent_m4a, gen=1)
        deadline = time.time() + 2.0
        while time.time() < deadline and engine._queue is None:  # noqa: SLF001
            time.sleep(0.01)
        first_queue = engine._queue  # noqa: SLF001
        first_stop = engine._decoder_stop  # noqa: SLF001
        assert first_queue is not None
        assert first_stop is not None

        _play(engine, silent_m4a, gen=2)
        deadline = time.time() + 2.0
        while time.time() < deadline and (engine._queue is first_queue or engine._queue is None):  # noqa: SLF001
            time.sleep(0.01)
        assert engine._queue is not first_queue, "queue must be re-created per playback"  # noqa: SLF001
        assert engine._decoder_stop is not first_stop, "stop event must be re-created per playback"  # noqa: SLF001
        assert first_stop.is_set(), "old decoder's stop event must be set so it bails on its next iteration"
    finally:
        engine.teardown()


def test_stop_cancels_pending_async_open(monkeypatch: pytest.MonkeyPatch, fake_stream: type[_FakeOutputStream]) -> None:
    """`play(slow_url); stop()` must NOT start playback after the slow open returns.

    Regression: stop() previously didn't bump the generation, so the opener
    thread's stale-gen check still passed and _setup_playback fired post-stop.
    """
    from musickit.tui import audio_engine as engine_mod

    open_started = threading.Event()
    release = threading.Event()

    def slow_open(_source: object) -> tuple[Any, Any]:
        open_started.set()
        # Block until the test releases — simulating a slow HTTP connect.
        release.wait(timeout=5.0)
        # Returning here would mean the opener completes; a stale-gen check
        # should have invalidated it so `_setup_playback` never runs.
        raise AssertionError("opener completed but stop() should have invalidated it")

    monkeypatch.setattr(engine_mod, "open_container", slow_open)

    engine, _state, events = _make_engine()
    try:
        _play(engine, "http://slow-stream.example/test", gen=1)
        assert open_started.wait(timeout=2.0), "opener thread never ran"

        engine.dispatch(Command(op=Op.STOP))
        release.set()
        time.sleep(0.2)

        # The opener bails silently when its gen is stale — no TRACK_FAILED.
        assert _drain_until(events, EventOp.TRACK_FAILED, timeout=0.3) is None
    finally:
        engine.teardown()


def test_engine_seek_clamps_to_duration(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    engine, state, _events = _make_engine()
    try:
        _play(engine, silent_m4a)
        deadline = time.time() + 2.0
        while time.time() < deadline and state.duration_s.value == 0:
            time.sleep(0.05)
        assert state.duration_s.value > 0
        engine.dispatch(Command(op=Op.SEEK, payload=9999.0))
        # No exception — clamp + decoder seek path didn't fall over.
    finally:
        engine.teardown()


# ---------------------------------------------------------------------------
# AudioPlayer surface (smoke only — no subprocess to avoid spawning per-test)
# ---------------------------------------------------------------------------


def test_player_volume_clamps() -> None:
    """Volume clamping is pure shared-memory state — no engine work needed."""
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    try:
        player.set_volume(150)
        assert player.volume == 100
        player.set_volume(-10)
        assert player.volume == 0
        player.set_volume(50)
        assert player.volume == 50
    finally:
        player.shutdown()


# Silence pytest unused-import noise.
_ = (Any, Callable, MagicMock, patch)
