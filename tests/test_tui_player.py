"""AudioPlayer: PyAV decoder thread + sounddevice callback (stubbed)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from musickit import convert as convert_mod


@pytest.fixture
def silent_m4a(silent_flac_template: Path, tmp_path: Path) -> Path:
    """A short silent .m4a track produced by the existing convert helper."""
    dst = tmp_path / "silent.m4a"
    convert_mod.to_alac(silent_flac_template, dst)
    return dst


class _FakeOutputStream:
    """Stand-in for `sounddevice.OutputStream` that drives the callback in a thread.

    The real OutputStream opens an audio device. We don't want that in tests,
    but we do want to exercise the callback so end-of-track + position
    accounting are tested for real.
    """

    def __init__(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: Callable[..., None],
        blocksize: int,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
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


def test_player_decodes_silent_m4a(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Smoke test: open a real m4a, decoder thread + callback hand off bytes."""
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.play(silent_m4a)

    # Wait until the player has consumed something.
    deadline = time.time() + 5.0
    while time.time() < deadline and player.position == 0:
        time.sleep(0.05)
    assert player.position > 0, "callback never ran"
    assert player.duration > 0, "duration not derived from PyAV stream"
    assert player.is_playing
    player.stop()


def test_player_track_end_callback_fires(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Decoder finishes + queue drains → on_track_end fires exactly once."""
    from musickit.tui.player import AudioPlayer

    fired = threading.Event()
    call_count = {"n": 0}

    def on_end() -> None:
        call_count["n"] += 1
        fired.set()

    player = AudioPlayer()
    player.on_track_end = on_end
    player.play(silent_m4a)

    assert fired.wait(timeout=10.0), "track-end never fired for a 0.2s silent track"
    # Debounce: callback must not fire twice even if more empties come in.
    time.sleep(0.2)
    assert call_count["n"] == 1
    player.stop()


def test_player_pause_writes_silence(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """Pause flag freezes the position counter (callback returns silence)."""
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.play(silent_m4a)
    # Let the callback run a few times.
    time.sleep(0.05)
    player.toggle_pause()
    snapshot = player.position
    time.sleep(0.1)
    # Position should have advanced ≤ a tiny amount (one in-flight callback).
    assert player.position - snapshot < 0.05
    assert player.is_paused
    player.stop()


def test_player_volume_clamps(fake_stream: type[_FakeOutputStream]) -> None:
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.set_volume(150)
    assert player.volume == 100
    player.set_volume(-10)
    assert player.volume == 0
    player.set_volume(50)
    assert player.volume == 50


def test_player_handles_unopenable_file_softly(tmp_path: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """A corrupt / non-audio file must surface as a soft callback, not an exception."""
    from musickit.tui.player import AudioPlayer

    bogus = tmp_path / "not-audio.m4a"
    bogus.write_bytes(b"\x00" * 100)
    failures: list[tuple[Path | str, str]] = []

    player = AudioPlayer()
    player.on_track_failed = lambda p, msg: failures.append((p, msg))
    player.play(bogus)
    # `play()` is now threaded — wait for the opener thread to finish.
    deadline = time.time() + 5.0
    while time.time() < deadline and not failures:
        time.sleep(0.05)
    assert len(failures) == 1
    assert failures[0][0] == bogus


def test_player_stop_is_safe_when_never_played(fake_stream: type[_FakeOutputStream]) -> None:
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.stop()  # must not raise


def test_stale_decoder_does_not_corrupt_next_playback(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    """A slow-to-exit decoder must write to its own queue, not the next track's.

    Regression: previously the decoder thread read self._queue + self._stopped
    by reference. After teardown reset those to point at a new playback's
    state, the stale decoder could push old PCM (or its end-of-stream
    sentinel) into the new playback's queue, corrupting the next track.
    """
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.play(silent_m4a)
    # Let the decoder spin up.
    deadline = time.time() + 2.0
    while time.time() < deadline and player._queue is None:  # noqa: SLF001
        time.sleep(0.01)
    first_queue = player._queue  # noqa: SLF001
    first_stop = player._decoder_stop  # noqa: SLF001
    assert first_queue is not None
    assert first_stop is not None

    # Tear down + start a new playback. The new one must get fresh
    # queue + stop event objects; the OLD ones must have been signalled.
    player.play(silent_m4a)
    deadline = time.time() + 2.0
    while time.time() < deadline and (player._queue is first_queue or player._queue is None):  # noqa: SLF001
        time.sleep(0.01)
    assert player._queue is not first_queue, "queue must be re-created per playback"  # noqa: SLF001
    assert player._decoder_stop is not first_stop, "stop event must be re-created per playback"  # noqa: SLF001
    assert first_stop.is_set(), "old decoder's stop event must be set so it bails on its next iteration"

    player.stop()


def test_stop_cancels_pending_async_open(monkeypatch: pytest.MonkeyPatch, fake_stream: type[_FakeOutputStream]) -> None:
    """`play(slow_url); stop()` must NOT start playback after the slow open returns.

    Regression: stop() previously didn't bump _opener_gen, so the opener
    thread's stale-gen check still passed and _setup_playback fired post-stop.
    """
    from musickit.tui import player as player_mod
    from musickit.tui.player import AudioPlayer

    open_started = threading.Event()
    release = threading.Event()

    def slow_open(_source: object) -> tuple[Any, Any]:
        open_started.set()
        # Block until the test releases — simulating a slow HTTP connect.
        release.wait(timeout=5.0)
        # Return a no-op container/stream pair. _setup_playback would call
        # `stream.duration`, `container.metadata` etc. — but it should never
        # be reached because stop() bumped the generation.
        raise AssertionError("opener completed but stop() should have invalidated it")

    monkeypatch.setattr(player_mod, "open_container", slow_open)

    player = AudioPlayer()
    failures: list[str] = []
    player.on_track_failed = lambda _p, msg: failures.append(msg)

    player.play("http://slow-stream.example/test")
    assert open_started.wait(timeout=2.0), "opener thread never ran"

    player.stop()
    release.set()

    # Give the opener thread a moment to wake up post-stop and (correctly) bail.
    time.sleep(0.2)
    # No on_track_failed: the opener bails silently when its gen is stale,
    # rather than firing a failure callback.
    assert failures == []


def test_player_seek_clamps_to_duration(silent_m4a: Path, fake_stream: type[_FakeOutputStream]) -> None:
    from musickit.tui.player import AudioPlayer

    player = AudioPlayer()
    player.play(silent_m4a)
    # Wait until duration is populated.
    deadline = time.time() + 2.0
    while time.time() < deadline and player.duration == 0:
        time.sleep(0.05)
    assert player.duration > 0
    player.seek(9999.0)
    # No exception means the clamp + decoder seek path didn't fall over.
    player.stop()


# Silence pytest unused-import noise.
_ = (Any, Path, Callable, patch)
