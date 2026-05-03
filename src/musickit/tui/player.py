"""In-process audio playback via PyAV (decoding) + sounddevice (output).

Runs the decoder in a worker thread that pushes resampled float32 stereo
chunks into a bounded queue. The sounddevice output stream callback drains
one chunk per call and applies a software volume gain. Pause writes silence;
seek flushes the queue and asks PyAV to seek the underlying container.

Track-end is event-driven: when the decoder thread finishes AND the queue
is empty, we fire `on_track_end` once.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import av
import av.error
import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from av.audio.frame import AudioFrame
    from av.audio.resampler import AudioResampler
    from av.container.input import InputContainer

log = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_CHANNELS = 2
_DTYPE = "float32"
_QUEUE_MAX_CHUNKS = 128  # ~3 seconds of buffered float32 stereo at 44.1kHz
_CHUNK_FRAMES = 1024  # frames per output callback iteration
_VIS_BANDS = 24
_VIS_DECAY = 0.85  # per-callback decay of the band level (smooths the bars)


class AudioPlayer:
    """Play audio files via PyAV → sounddevice with pause/seek/volume.

    Threading model:
      - Caller thread: `play()`, `stop()`, `toggle_pause()`, `seek()`, `set_volume()`.
      - Decoder thread (one per `play()`): reads packets, decodes, resamples, queues PCM.
      - Audio callback thread (sounddevice-managed): drains the queue.

    All shared state is guarded by `self._lock`.
    """

    on_track_end: Callable[[], None] | None = None
    on_track_failed: Callable[[Path, str], None] | None = None

    def __init__(self, sample_rate: int = _SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._volume: float = 1.0
        self._paused: bool = False
        self._stopped: bool = True
        self._frames_played: int = 0
        self._duration: float = 0.0
        self._current_path: Path | None = None
        # 8-band amplitude levels (0.0–1.0), driven by the UI tick (NOT the
        # audio callback). The callback only stores the last-played chunk;
        # `update_band_levels()` reads it and runs FFT off the audio thread.
        self._band_levels: list[float] = [0.0] * _VIS_BANDS
        self._latest_chunk: np.ndarray | None = None
        # Carry state for the audio callback: sounddevice's `frames` per
        # call is a hint, not a guarantee, and may differ from our
        # decoder's chunk size. We keep the partially-consumed current
        # chunk between callbacks so no PCM is dropped or zero-padded.
        self._current_chunk: np.ndarray | None = None
        self._chunk_offset: int = 0
        # Filled per-`play()`:
        self._queue: queue.Queue[np.ndarray | None] | None = None
        self._decoder_thread: threading.Thread | None = None
        self._stream: sd.OutputStream | None = None
        self._end_fired: bool = False
        self._seek_target: float | None = None  # set by seek(), consumed by decoder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def play(self, path: Path) -> None:
        """Stop any current playback and start `path` from the beginning."""
        self.stop()
        self._frames_played = 0
        self._end_fired = False
        self._paused = False
        self._stopped = False
        self._current_path = path
        self._queue = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        self._current_chunk = None
        self._chunk_offset = 0

        try:
            container, stream = _open_container(path)
        except Exception as exc:
            log.warning("failed to open %s: %s", path, exc)
            self._stopped = True
            if self.on_track_failed is not None:
                self.on_track_failed(path, str(exc))
            return

        self._duration = float(stream.duration * stream.time_base) if stream.duration else 0.0

        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            args=(container, stream),
            name=f"musickit-decoder-{path.name}",
            daemon=True,
        )
        self._decoder_thread.start()

        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=_CHANNELS,
                dtype=_DTYPE,
                callback=self._audio_callback,
                blocksize=_CHUNK_FRAMES,
            )
            self._stream.start()
        except Exception as exc:
            log.warning("failed to open audio device: %s", exc)
            self._stopped = True
            if self.on_track_failed is not None:
                self.on_track_failed(path, f"audio device unavailable: {exc}")

    def stop(self) -> None:
        """Stop playback and join the decoder thread."""
        with self._lock:
            self._stopped = True
            self._band_levels = [0.0] * _VIS_BANDS
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
            self._stream = None
        # Drain the queue so the decoder unblocks on its `put`.
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
        self._current_chunk = None
        self._chunk_offset = 0

    def toggle_pause(self) -> None:
        with self._lock:
            self._paused = not self._paused

    def seek(self, seconds: float) -> None:
        """Seek to absolute position `seconds` from start."""
        if self._duration <= 0:
            return
        with self._lock:
            self._seek_target = max(0.0, min(seconds, self._duration))

    def set_volume(self, percent: int) -> None:
        with self._lock:
            self._volume = max(0.0, min(1.0, percent / 100.0))

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        return self._frames_played / self._sample_rate

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return not self._stopped and not self._paused

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def volume(self) -> int:
        with self._lock:
            return int(round(self._volume * 100))

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def band_levels(self) -> list[float]:
        """8 amplitude bins (0.0–1.0) for the spectrum visualizer.

        Updated by the audio callback (cheap FFT per chunk). Read by the UI
        thread on its render tick. No lock needed — list reads are atomic for
        small fixed-size lists in CPython, and we're rendering for visuals.
        """
        return list(self._band_levels)

    # ------------------------------------------------------------------
    # Decoder thread
    # ------------------------------------------------------------------

    def _decoder_loop(self, container: InputContainer, stream: Any) -> None:
        try:
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=self._sample_rate)
            self._decode_into_queue(container, stream, resampler)
        except Exception as exc:  # pragma: no cover — surface decode errors softly
            log.warning("decoder failed for %s: %s", self._current_path, exc)
            if self.on_track_failed is not None and self._current_path is not None:
                self.on_track_failed(self._current_path, str(exc))
        finally:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            # Sentinel: tells the audio callback no more chunks are coming.
            if self._queue is not None:
                try:
                    self._queue.put(None, timeout=1.0)
                except queue.Full:  # pragma: no cover
                    pass

    def _decode_into_queue(self, container: InputContainer, stream: Any, resampler: AudioResampler) -> None:
        for packet in container.demux(stream):
            if self._stopped:
                return
            # Honour seeks issued from the caller thread.
            target = self._consume_seek_target()
            if target is not None:
                self._apply_seek(container, stream, target)
                continue
            for frame in packet.decode():
                if self._stopped:
                    return
                for resampled in resampler.resample(frame):
                    self._push_frame(resampled)
                    if self._stopped:
                        return

    def _consume_seek_target(self) -> float | None:
        with self._lock:
            target = self._seek_target
            self._seek_target = None
        return target

    def _apply_seek(self, container: InputContainer, stream: Any, seconds: float) -> None:
        # Drop queued PCM so the next callback hears the new position immediately.
        if self._queue is not None:
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        target_pts = int(seconds / float(stream.time_base))
        try:
            container.seek(target_pts, stream=stream)
        except av.error.FFmpegError:  # pragma: no cover
            return
        self._frames_played = int(seconds * self._sample_rate)

    def _push_frame(self, frame: AudioFrame) -> None:
        if self._queue is None:
            return
        # PyAV gives us (channels, samples) when planar, (1, samples*channels) when packed.
        # Resampler with format='flt' (float interleaved) → packed.
        array = frame.to_ndarray()
        if array.ndim == 2 and array.shape[0] == 1:
            # Packed float: (1, samples * channels). Reshape to (samples, channels).
            interleaved = array[0].reshape(-1, _CHANNELS)
        else:
            # Defensive: planar fallback — transpose to (samples, channels).
            interleaved = array.T.copy()
        # Push in fixed-size chunks so the audio callback always gets _CHUNK_FRAMES.
        offset = 0
        total = interleaved.shape[0]
        while offset < total and not self._stopped:
            chunk = interleaved[offset : offset + _CHUNK_FRAMES]
            offset += chunk.shape[0]
            try:
                self._queue.put(np.ascontiguousarray(chunk, dtype=np.float32), timeout=1.0)
            except queue.Full:  # pragma: no cover
                if self._stopped:
                    return

    # ------------------------------------------------------------------
    # Audio callback (runs on the sounddevice thread)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        del time_info, status
        if self._stopped or self._queue is None:
            outdata.fill(0)
            return
        with self._lock:
            paused = self._paused
            volume = self._volume
        if paused:
            outdata.fill(0)
            return
        # Drain across however many decoder chunks are needed to fill `frames`.
        # `frames` is sounddevice's request size and is NOT guaranteed to
        # equal the decoder's chunk size (`_CHUNK_FRAMES`) — `blocksize` is a
        # hint. Pulling exactly one chunk per callback caused dropped samples
        # (chunk too big) or silence padding (chunk too small), perceptually
        # producing slow/stuttery playback. Carry partially-consumed chunks
        # between callbacks via `self._current_chunk` + `self._chunk_offset`.
        written = 0
        while written < frames:
            if self._current_chunk is None or self._chunk_offset >= self._current_chunk.shape[0]:
                try:
                    next_chunk = self._queue.get_nowait()
                except queue.Empty:
                    outdata[written:].fill(0)
                    self._frames_played += written
                    return
                if next_chunk is None:
                    # End-of-stream sentinel — fill remainder with silence.
                    outdata[written:].fill(0)
                    self._frames_played += written
                    if not self._end_fired:
                        self._end_fired = True
                        if self.on_track_end is not None:
                            # Fire on a separate thread so the callback returns promptly.
                            threading.Thread(target=self.on_track_end, daemon=True).start()
                    return
                self._current_chunk = next_chunk
                self._chunk_offset = 0
            chunk = self._current_chunk
            available = chunk.shape[0] - self._chunk_offset
            take = min(available, frames - written)
            outdata[written : written + take] = chunk[self._chunk_offset : self._chunk_offset + take] * volume
            self._chunk_offset += take
            written += take
        self._frames_played += written
        # Stash the most recently delivered window for the UI thread's FFT.
        if self._current_chunk is not None and self._chunk_offset > 0:
            self._latest_chunk = self._current_chunk[max(0, self._chunk_offset - frames) : self._chunk_offset]

    def update_band_levels(self) -> None:
        """Compute FFT band levels from the most recently played chunk.

        Called from the UI thread (~30Hz) — explicitly NOT from the audio
        callback. If no fresh chunk is available (paused / between tracks),
        bars decay toward silence.
        """
        chunk = self._latest_chunk
        if chunk is None or chunk.size == 0:
            for i in range(_VIS_BANDS):
                self._band_levels[i] *= _VIS_DECAY
            return
        mono = chunk.mean(axis=1) if chunk.ndim == 2 else chunk
        spectrum = np.abs(np.fft.rfft(mono))
        n_bins = spectrum.shape[0]
        edges = self._band_edges_for(n_bins)
        for i in range(_VIS_BANDS):
            lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
            peak = float(spectrum[lo:hi].max()) if hi > lo else 0.0
            level = min(1.0, peak / 32.0)
            prev = self._band_levels[i]
            self._band_levels[i] = max(level, prev * _VIS_DECAY)

    _cached_edges: np.ndarray | None = None
    _cached_edges_n: int = 0

    def _band_edges_for(self, n_bins: int) -> np.ndarray:
        if self._cached_edges is None or self._cached_edges_n != n_bins:
            edges = np.geomspace(1, n_bins, _VIS_BANDS + 1).astype(int)
            self._cached_edges = np.clip(edges, 1, n_bins)
            self._cached_edges_n = n_bins
        return self._cached_edges


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_container(path: Path) -> tuple[InputContainer, Any]:
    """Open `path` and return `(container, audio_stream)`. Caller owns the container."""
    container = av.open(str(path))
    audio_streams = [s for s in container.streams if s.type == "audio"]
    if not audio_streams:
        container.close()
        raise ValueError(f"no audio stream in {path}")
    return container, audio_streams[0]
