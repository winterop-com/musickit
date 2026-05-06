"""AudioEngine: device-unavailable recovery.

`_setup_playback` opens an `sd.OutputStream`. If the device is gone
(headphones unplugged at boot, machine has no audio output, etc.) the
constructor raises. The engine catches that, tears down the just-spun
decoder thread + queue, and emits TRACK_FAILED so the UI can show the
error. Without the teardown we'd leak ~12 s of float32 stereo per
failed play(); without the TRACK_FAILED the UI would silently sit at
"playing" forever.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from musickit.tui.audio_proto import EventOp
from tests.test_tui_player import _drain_until, _make_engine, _play


class _FailingOutputStream:
    """Drop-in for `sd.OutputStream` whose constructor always raises."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError("PortAudio: no default output device")


@pytest.fixture
def failing_stream(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Replace `sd.OutputStream` with a constructor that always raises."""
    import sounddevice as sd  # type: ignore[import-untyped]

    monkeypatch.setattr(sd, "OutputStream", _FailingOutputStream)
    return lambda: None


def test_device_unavailable_emits_track_failed(silent_m4a: Path, failing_stream: Callable[[], None]) -> None:
    """A failed `OutputStream` open surfaces as TRACK_FAILED, not a process crash."""
    del failing_stream
    engine, _state, events = _make_engine()
    try:
        _play(engine, silent_m4a, gen=1)
        failed = _drain_until(events, EventOp.TRACK_FAILED, timeout=3.0)
        assert failed is not None, "TRACK_FAILED should fire when the audio device fails to open"
        message = failed.payload["message"]
        assert "audio device unavailable" in message.lower()
    finally:
        engine.teardown()


def test_device_unavailable_tears_down_decoder(silent_m4a: Path, failing_stream: Callable[[], None]) -> None:
    """No leaked decoder thread / queue after a failed device open."""
    del failing_stream
    engine, _state, _events = _make_engine()
    try:
        _play(engine, silent_m4a, gen=1)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if engine._decoder_thread is None and engine._queue is None:  # noqa: SLF001
                break
            time.sleep(0.05)

        assert engine._decoder_thread is None, "decoder thread should be torn down on device failure"  # noqa: SLF001
        assert engine._queue is None, "decoder queue should be cleared on device failure"  # noqa: SLF001
    finally:
        engine.teardown()


def test_engine_state_not_poisoned_after_device_failure(
    silent_m4a: Path,
    failing_stream: Callable[[], None],
) -> None:
    """After a failed play, _opener_gen is still incrementable + a new play sets up cleanly.

    Doesn't try to actually play through a working device — that's flaky
    under thread contention. Just asserts the engine isn't stuck in a
    half-initialised state where the next dispatch can't proceed.
    """
    del failing_stream
    engine, _state, events = _make_engine()
    try:
        _play(engine, silent_m4a, gen=1)
        first_fail = _drain_until(events, EventOp.TRACK_FAILED, timeout=8.0)
        assert first_fail is not None

        # A second PLAY (still a failing device) — gen bookkeeping should
        # advance, opener thread should fire, no exception.
        _play(engine, silent_m4a, gen=2)
        assert engine._opener_gen == 2  # noqa: SLF001
        second_fail = _drain_until(events, EventOp.TRACK_FAILED, timeout=8.0)
        assert second_fail is not None, "engine must keep accepting plays after a prior failure"
        assert second_fail.gen == 2
    finally:
        engine.teardown()
