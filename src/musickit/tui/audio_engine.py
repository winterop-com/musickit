"""Audio engine that runs in a separate process — PyAV decoder + sounddevice output.

Spawned by `AudioPlayer.__init__`. Receives `Command`s on a
`multiprocessing.Queue`, emits `Event`s on a second queue, and
publishes high-frequency state (position, band levels) into shared
memory so the UI can read it without round-tripping through IPC.

Why a subprocess: the sounddevice audio callback is implemented in
Python and acquires the GIL on every fire. A burst of UI work in the
main interpreter (Textual reflows, focus changes, GC) used to stall
the callback past the device-buffer deadline, producing audible
clicks (xruns). With the engine in its own interpreter the audio
callback only contends with the decoder thread inside this process
and is unaffected by the UI's GIL pressure.

The engine talks back via three lanes:

  - `event_queue` — low-rate events (track_end, track_failed,
    metadata_changed, started). The UI has a reader thread.
  - `state` — `multiprocessing.Value` / `Array` references for
    position, paused/stopped flags, band levels, volume,
    replaygain_multiplier. Atomic reads/writes; no lock contention
    on the hot path.
  - exit code — process exit signals catastrophic failure.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import av
import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]

from musickit.tui.audio_io import get_metadata_value, open_container
from musickit.tui.audio_proto import (
    CHANNELS,
    SAMPLE_RATE,
    VIS_BANDS,
    Command,
    Event,
    EventOp,
    Op,
    PlayPayload,
    StartedPayload,
)

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as MPQueue
    from multiprocessing.sharedctypes import Synchronized, SynchronizedArray

    from av.audio.frame import AudioFrame
    from av.audio.resampler import AudioResampler
    from av.container.input import InputContainer

log = logging.getLogger(__name__)

# Same buffering parameters as the previous in-process player.
_QUEUE_MAX_CHUNKS = 512
_CHUNK_FRAMES = 1024
_PREBUFFER_CHUNKS = 8
_PREBUFFER_TIMEOUT_S = 1.5
_VIS_DECAY = 0.85


@dataclass
class SharedState:
    """References to multiprocessing.Value / Array shared with the UI process.

    All fields are `multiprocessing.Value` / `multiprocessing.Array`
    handles. Reads/writes are atomic at the slot level — no extra locks
    needed for these scalars.
    """

    position_frames: Synchronized[int]  # int64, frames played at output rate
    duration_s: Synchronized[float]
    paused: Synchronized[int]  # 0/1
    stopped: Synchronized[int]  # 0/1; 1 == no active playback
    is_live: Synchronized[int]  # 0/1
    volume: Synchronized[float]  # 0.0–1.0 multiplier
    replaygain_multiplier: Synchronized[float]
    band_levels: SynchronizedArray[float]  # length VIS_BANDS


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------


def engine_main(
    cmd_queue: MPQueue[Command],
    event_queue: MPQueue[Event],
    state: SharedState,
) -> None:
    """Subprocess entry point — runs until SHUTDOWN."""
    # Best-effort logging setup so warnings show up in the parent's stderr.
    logging.basicConfig(level=logging.WARNING, format="audio-engine %(levelname)s: %(message)s")
    engine = AudioEngine(event_queue, state)
    try:
        while True:
            try:
                cmd = cmd_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if cmd.op is Op.SHUTDOWN:
                break
            try:
                engine.dispatch(cmd)
            except Exception:  # pragma: no cover — engine must keep running
                log.exception("engine dispatch failed for %s", cmd.op)
    finally:
        engine.teardown()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AudioEngine:
    """Owns PyAV container, decoder thread, sounddevice output stream."""

    def __init__(self, event_queue: MPQueue[Event], state: SharedState) -> None:
        self._events = event_queue
        self._state = state
        self._sample_rate = SAMPLE_RATE

        self._lock = threading.Lock()
        self._stream_title: str | None = None
        self._stream_station_name: str | None = None
        self._end_fired = False
        self._seek_target: float | None = None

        self._latest_chunk: np.ndarray | None = None
        self._current_chunk: np.ndarray | None = None
        self._chunk_offset = 0
        self._band_levels = [0.0] * VIS_BANDS

        self._queue: queue.Queue[np.ndarray | None] | None = None
        self._decoder_thread: threading.Thread | None = None
        self._decoder_stop: threading.Event | None = None
        self._stream: sd.OutputStream | None = None
        self._opener_gen = 0
        self._current_source: str | None = None
        self._cached_edges: np.ndarray | None = None
        self._cached_edges_n = 0

        self._set_stopped(True)
        self._set_paused(False)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def dispatch(self, cmd: Command) -> None:
        if cmd.op is Op.PLAY:
            assert isinstance(cmd.payload, PlayPayload)
            self._play(cmd.payload, cmd.gen)
        elif cmd.op is Op.STOP:
            self._stop()
        elif cmd.op is Op.TOGGLE_PAUSE:
            self._toggle_pause()
        elif cmd.op is Op.SEEK:
            self._seek(float(cmd.payload))
        elif cmd.op is Op.SET_REPLAYGAIN_MODE:
            # Multiplier itself is computed UI-side and pushed into shared
            # memory; this opcode is reserved for any future engine-side
            # bookkeeping.
            pass

    def teardown(self) -> None:
        self._teardown_playback()

    # ------------------------------------------------------------------
    # PLAY / STOP
    # ------------------------------------------------------------------

    def _play(self, payload: PlayPayload, gen: int) -> None:
        # Bump generation; spawn an opener thread. The opener tears down the
        # previous playback only after the new container is ready, so the
        # previous track keeps playing during a slow HTTP connect.
        self._opener_gen = gen
        source = Path(payload.source) if payload.is_path else payload.source
        threading.Thread(
            target=self._open_and_swap,
            args=(source, gen),
            name=f"musickit-opener-{Path(str(source)).name}",
            daemon=True,
        ).start()

    def _open_and_swap(self, source: Path | str, gen: int) -> None:
        try:
            container, stream = open_container(source)
        except Exception as exc:
            log.warning("failed to open %s: %s", source, exc)
            if gen == self._opener_gen:
                self._emit(EventOp.TRACK_FAILED, {"source": str(source), "message": str(exc)})
            return
        if gen != self._opener_gen:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            return
        self._teardown_playback()
        if gen != self._opener_gen:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            return
        self._setup_playback(source, container, stream)

    def _setup_playback(self, source: Path | str, container: InputContainer, stream: Any) -> None:
        self._end_fired = False
        self._set_paused(False)
        self._set_stopped(False)
        self._current_source = str(source)
        with self._state.position_frames.get_lock():
            self._state.position_frames.value = 0

        local_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        local_stop = threading.Event()
        self._queue = local_queue
        self._decoder_stop = local_stop
        self._current_chunk = None
        self._chunk_offset = 0

        duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
        is_live = duration <= 0
        with self._state.duration_s.get_lock():
            self._state.duration_s.value = duration
        with self._state.is_live.get_lock():
            self._state.is_live.value = 1 if is_live else 0
        self._stream_station_name = get_metadata_value(container, "icy-name")
        self._stream_title = get_metadata_value(container, "StreamTitle")

        # Tell the UI the new track is opened so it can refresh its cached
        # source / duration / station-name immediately.
        self._emit(
            EventOp.STARTED,
            StartedPayload(
                source=str(source),
                is_path=isinstance(source, Path),
                duration_s=duration,
                is_live=is_live,
                stream_title=self._stream_title,
                stream_station_name=self._stream_station_name,
                sample_rate=self._sample_rate,
            ),
        )

        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            args=(container, stream, local_queue, local_stop, source, is_live),
            name=f"musickit-decoder-{Path(str(source)).name}",
            daemon=True,
        )
        self._decoder_thread.start()

        # Prebuffer so the first audio callback finds chunks ready.
        deadline = time.time() + _PREBUFFER_TIMEOUT_S
        while time.time() < deadline and local_queue.qsize() < _PREBUFFER_CHUNKS and not local_stop.is_set():
            time.sleep(0.01)

        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=CHANNELS,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=_CHUNK_FRAMES,
                # 200ms buffer. The big buffer (1s) we needed when the
                # audio callback shared the GIL with Textual's render
                # loop is no longer relevant — this whole module runs in
                # a separate interpreter, so UI-side reflows / focus
                # changes / GC pauses can't stall the callback. 200ms is
                # plenty of headroom against decoder hiccups, and small
                # enough that the visualizer (which FFTs the chunk being
                # sent to PortAudio) doesn't run noticeably ahead of
                # what the user actually hears.
                latency=0.2,
            )
            self._stream.start()
        except Exception as exc:
            log.warning("failed to open audio device: %s", exc)
            self._set_stopped(True)
            self._emit(EventOp.TRACK_FAILED, {"source": str(source), "message": f"audio device unavailable: {exc}"})

    def _stop(self) -> None:
        self._opener_gen += 1
        self._teardown_playback()

    def _teardown_playback(self) -> None:
        self._set_stopped(True)
        for i in range(VIS_BANDS):
            self._band_levels[i] = 0.0
        self._publish_band_levels()
        if self._decoder_stop is not None:
            self._decoder_stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover
                pass
            self._stream = None
        if self._queue is not None:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        if self._decoder_thread is not None and self._decoder_thread.is_alive():
            self._decoder_thread.join(timeout=2.0)
        self._decoder_thread = None
        self._queue = None
        self._decoder_stop = None
        self._current_chunk = None
        self._chunk_offset = 0

    # ------------------------------------------------------------------
    # Pause / seek
    # ------------------------------------------------------------------

    def _toggle_pause(self) -> None:
        with self._lock:
            now_paused = not self._is_paused()
            self._set_paused(now_paused)

    def _seek(self, seconds: float) -> None:
        with self._state.duration_s.get_lock():
            duration = self._state.duration_s.value
        if duration <= 0:
            return
        with self._lock:
            self._seek_target = max(0.0, min(seconds, duration))

    # ------------------------------------------------------------------
    # Decoder thread
    # ------------------------------------------------------------------

    def _decoder_loop(
        self,
        container: InputContainer,
        stream: Any,
        local_queue: queue.Queue[np.ndarray | None],
        local_stop: threading.Event,
        source: Path | str,
        is_live: bool,
    ) -> None:
        try:
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=self._sample_rate)
            self._decode_into_queue(container, stream, resampler, local_queue, local_stop, is_live)
        except Exception as exc:  # pragma: no cover
            log.warning("decoder failed for %s: %s", source, exc)
            if not local_stop.is_set():
                self._emit(EventOp.TRACK_FAILED, {"source": str(source), "message": str(exc)})
        finally:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            try:
                local_queue.put(None, timeout=1.0)
            except queue.Full:  # pragma: no cover
                pass

    def _decode_into_queue(
        self,
        container: InputContainer,
        stream: Any,
        resampler: AudioResampler,
        local_queue: queue.Queue[np.ndarray | None],
        local_stop: threading.Event,
        is_live: bool,
    ) -> None:
        for packet in container.demux(stream):
            if local_stop.is_set():
                return
            target = self._consume_seek_target()
            if target is not None:
                self._apply_seek(container, stream, target, local_queue)
                continue
            if is_live:
                self._poll_stream_metadata(container)
            for frame in packet.decode():
                if local_stop.is_set():
                    return
                for resampled in resampler.resample(frame):
                    self._push_frame(resampled, local_queue, local_stop)
                    if local_stop.is_set():
                        return

    def _poll_stream_metadata(self, container: InputContainer) -> None:
        new_title = get_metadata_value(container, "StreamTitle")
        if new_title and new_title != self._stream_title:
            self._stream_title = new_title
            self._emit(
                EventOp.METADATA_CHANGED,
                {"stream_title": new_title, "stream_station_name": self._stream_station_name},
            )

    def _consume_seek_target(self) -> float | None:
        with self._lock:
            target = self._seek_target
            self._seek_target = None
        return target

    def _apply_seek(
        self,
        container: InputContainer,
        stream: Any,
        seconds: float,
        local_queue: queue.Queue[np.ndarray | None],
    ) -> None:
        while True:
            try:
                local_queue.get_nowait()
            except queue.Empty:
                break
        target_pts = int(seconds / float(stream.time_base))
        try:
            container.seek(target_pts, stream=stream)
        except Exception:  # pragma: no cover — av.error.FFmpegError + transport errors
            return
        with self._state.position_frames.get_lock():
            self._state.position_frames.value = int(seconds * self._sample_rate)

    def _push_frame(
        self,
        frame: AudioFrame,
        local_queue: queue.Queue[np.ndarray | None],
        local_stop: threading.Event,
    ) -> None:
        array = frame.to_ndarray()
        if array.ndim == 2 and array.shape[0] == 1:
            interleaved = array[0].reshape(-1, CHANNELS)
        else:
            interleaved = array.T.copy()
        offset = 0
        total = interleaved.shape[0]
        while offset < total and not local_stop.is_set():
            chunk = interleaved[offset : offset + _CHUNK_FRAMES]
            offset += chunk.shape[0]
            try:
                local_queue.put(np.ascontiguousarray(chunk, dtype=np.float32), timeout=1.0)
            except queue.Full:  # pragma: no cover
                if local_stop.is_set():
                    return

    # ------------------------------------------------------------------
    # Audio callback
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        del time_info, status
        if self._is_stopped() or self._queue is None:
            outdata.fill(0)
            return
        if self._is_paused():
            outdata.fill(0)
            return
        with self._state.volume.get_lock():
            volume = self._state.volume.value
        with self._state.replaygain_multiplier.get_lock():
            rg = self._state.replaygain_multiplier.value
        gain = volume * rg

        written = 0
        while written < frames:
            if self._current_chunk is None or self._chunk_offset >= self._current_chunk.shape[0]:
                try:
                    next_chunk = self._queue.get_nowait()
                except queue.Empty:
                    outdata[written:].fill(0)
                    self._advance_position(written)
                    return
                if next_chunk is None:
                    outdata[written:].fill(0)
                    self._advance_position(written)
                    if not self._end_fired:
                        self._end_fired = True
                        self._emit(EventOp.TRACK_END, None)
                    return
                self._current_chunk = next_chunk
                self._chunk_offset = 0
            chunk = self._current_chunk
            available = chunk.shape[0] - self._chunk_offset
            take = min(available, frames - written)
            outdata[written : written + take] = chunk[self._chunk_offset : self._chunk_offset + take] * gain
            self._chunk_offset += take
            written += take
        self._advance_position(written)
        if self._current_chunk is not None and self._chunk_offset > 0:
            self._latest_chunk = self._current_chunk[max(0, self._chunk_offset - frames) : self._chunk_offset]
        # Update the FFT bands and publish into shared memory at audio rate.
        # Cheap (24 bins, geomspace edges, vectorised np.fft) compared to
        # the decode path; doesn't dominate callback time.
        self._update_band_levels()
        self._publish_band_levels()

    # ------------------------------------------------------------------
    # Visualizer
    # ------------------------------------------------------------------

    def _update_band_levels(self) -> None:
        chunk = self._latest_chunk
        if chunk is None or chunk.size == 0:
            for i in range(VIS_BANDS):
                self._band_levels[i] *= _VIS_DECAY
            return
        mono = chunk.mean(axis=1) if chunk.ndim == 2 else chunk
        spectrum = np.abs(np.fft.rfft(mono))
        n_bins = spectrum.shape[0]
        edges = self._band_edges_for(n_bins)
        for i in range(VIS_BANDS):
            lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
            peak = float(spectrum[lo:hi].max()) if hi > lo else 0.0
            level = min(1.0, peak / 32.0)
            prev = self._band_levels[i]
            self._band_levels[i] = max(level, prev * _VIS_DECAY)

    def _band_edges_for(self, n_bins: int) -> np.ndarray:
        if self._cached_edges is None or self._cached_edges_n != n_bins:
            edges = np.geomspace(1, n_bins, VIS_BANDS + 1).astype(int)
            self._cached_edges = np.clip(edges, 1, n_bins)
            self._cached_edges_n = n_bins
        return self._cached_edges

    def _publish_band_levels(self) -> None:
        # The Array's lock is acquired once; the slot writes don't release
        # the GIL between iterations so this is effectively atomic from the
        # UI side too.
        with self._state.band_levels.get_lock():
            for i, level in enumerate(self._band_levels):
                self._state.band_levels[i] = level

    # ------------------------------------------------------------------
    # Shared-state helpers
    # ------------------------------------------------------------------

    def _set_paused(self, value: bool) -> None:
        with self._state.paused.get_lock():
            self._state.paused.value = 1 if value else 0

    def _is_paused(self) -> bool:
        with self._state.paused.get_lock():
            return bool(self._state.paused.value)

    def _set_stopped(self, value: bool) -> None:
        with self._state.stopped.get_lock():
            self._state.stopped.value = 1 if value else 0

    def _is_stopped(self) -> bool:
        with self._state.stopped.get_lock():
            return bool(self._state.stopped.value)

    def _advance_position(self, frames: int) -> None:
        with self._state.position_frames.get_lock():
            self._state.position_frames.value += frames

    def _emit(self, op: EventOp, payload: Any) -> None:
        try:
            self._events.put(Event(op=op, payload=payload), timeout=1.0)
        except Exception:  # pragma: no cover — UI side dropped the queue
            log.warning("failed to emit %s event", op)
