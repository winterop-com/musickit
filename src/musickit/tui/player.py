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
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import av
import av.error
import numpy as np
import sounddevice as sd  # type: ignore[import-untyped]

from musickit.tui.audio_io import get_metadata_value, open_container

if TYPE_CHECKING:
    from av.audio.frame import AudioFrame
    from av.audio.resampler import AudioResampler
    from av.container.input import InputContainer

    from musickit.tui.airplay import AirPlayController

log = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_CHANNELS = 2
_DTYPE = "float32"
# Buffer depth: 512 chunks × 1024 frames @ 44.1 kHz ≈ 12 seconds of stereo
# float32 audio (~4 MB resident). Tight 3-second buffers caused audible
# scratches on radio streams when WAN jitter spiked past the buffer drain
# rate — most often around 30s+ once the decoder's initial backlog cleared
# and steady-state network conditions started to bite. 12s is plenty of
# slack for transatlantic Icecast streams without feeling sluggish on
# local files (they fill the buffer in ~50ms regardless).
_QUEUE_MAX_CHUNKS = 512
_CHUNK_FRAMES = 1024  # frames per output callback iteration
# Wait for this many chunks to land in the queue before starting the audio
# stream. Without prebuffer the callback fires before the decoder has
# produced anything → first ~50ms of every track is silence-and-then-pop.
# 8 × 1024 frames @ 44.1 kHz ≈ 186ms — imperceptible startup latency for
# a clean attack. Capped to a small wait window so a slow stream open
# doesn't hold up forever.
_PREBUFFER_CHUNKS = 8
_PREBUFFER_TIMEOUT_S = 1.5
_VIS_BANDS = 24
_VIS_DECAY = 0.85  # per-callback decay of the band level (smooths the bars)

ReplayGainMode = str  # "auto" | "track" | "album" | "off"


def compute_replaygain_multiplier(replaygain: dict[str, str], mode: ReplayGainMode) -> float:
    """Translate ReplayGain tags into a linear amplitude multiplier.

    Modes:
      - "off"   → 1.0 (no normalisation)
      - "track" → use replaygain_track_gain only
      - "album" → use replaygain_album_gain only
      - "auto"  → prefer album, fall back to track

    Peak protection: when a peak value is also tagged, clamp the
    multiplier so peak * multiplier <= 1.0 — prevents the post-gain
    samples clipping past full scale.
    """
    if mode == "off" or not replaygain:
        return 1.0
    gain_str: str | None = None
    peak_str: str | None = None
    if mode == "track":
        gain_str = replaygain.get("replaygain_track_gain")
        peak_str = replaygain.get("replaygain_track_peak")
    elif mode == "album":
        gain_str = replaygain.get("replaygain_album_gain")
        peak_str = replaygain.get("replaygain_album_peak")
    else:  # "auto" or anything unrecognised
        gain_str = replaygain.get("replaygain_album_gain") or replaygain.get("replaygain_track_gain")
        peak_str = replaygain.get("replaygain_album_peak") or replaygain.get("replaygain_track_peak")
    if not gain_str:
        return 1.0
    gain_db = _parse_db(gain_str)
    if gain_db is None:
        return 1.0
    multiplier = 10 ** (gain_db / 20)
    if peak_str:
        try:
            peak = float(peak_str.strip())
        except ValueError:
            peak = 0.0
        if peak > 0:
            multiplier = min(multiplier, 1.0 / peak)
    return multiplier


def _parse_db(value: str) -> float | None:
    """Parse '-6.34 dB' / '-6.34 db' / '-6.34' → -6.34. None on failure."""
    s = value.strip().lower()
    if s.endswith("db"):
        s = s[:-2].strip()
    try:
        return float(s)
    except ValueError:
        return None


class AudioPlayer:
    """Play audio files via PyAV → sounddevice with pause/seek/volume.

    Threading model:
      - Caller thread: `play()`, `stop()`, `toggle_pause()`, `seek()`, `set_volume()`.
      - Decoder thread (one per `play()`): reads packets, decodes, resamples, queues PCM.
      - Audio callback thread (sounddevice-managed): drains the queue.

    All shared state is guarded by `self._lock`.
    """

    on_track_end: Callable[[], None] | None = None
    on_track_failed: Callable[[Path | str, str], None] | None = None
    on_metadata_change: Callable[[], None] | None = None

    def __init__(
        self,
        sample_rate: int = _SAMPLE_RATE,
        *,
        airplay: AirPlayController | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        # When set, `play(source)` hands the URL straight to the AirPlay
        # device instead of decoding locally. Only URL sources work — local
        # `Path` sources fall back to local playback (no inline HTTP server
        # in v1; the Subsonic-client mode covers the common remote case).
        self._airplay = airplay
        self._lock = threading.Lock()
        self._volume: float = 1.0
        # ReplayGain — mode set by the TUI (or via state.toml), multiplier
        # computed per track in `play()` from the LibraryTrack's tags.
        self._replaygain_mode: ReplayGainMode = "auto"
        self._replaygain_multiplier: float = 1.0
        self._paused: bool = False
        self._stopped: bool = True
        self._frames_played: int = 0
        self._duration: float = 0.0
        self._current_source: Path | str | None = None
        # Live-stream metadata. `is_live` flips True when we're playing a
        # URL whose `stream.duration` is None (Icecast/Shoutcast/HLS).
        # `stream_title` is the most recent ICY `StreamTitle` (current song)
        # — updated by the decoder thread polling `container.metadata`.
        self._is_live: bool = False
        self._stream_title: str | None = None
        self._stream_station_name: str | None = None
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
        # Per-decoder stop event. Each decoder thread captures its own
        # event by closure when started; teardown sets the OLD event so a
        # stale decoder that didn't join in time still bails on its next
        # iteration. Without this isolation, a slow decoder could wake up
        # after the next play() began and write to the new playback's
        # queue (queue corruption) or push an end-sentinel that the audio
        # callback would interpret as the new track having ended.
        self._decoder_stop: threading.Event | None = None
        self._stream: sd.OutputStream | None = None
        self._end_fired: bool = False
        self._seek_target: float | None = None  # set by seek(), consumed by decoder
        # Monotonic counter — every `play()` bumps it. The opener thread
        # checks against the latest value to drop stale opens when the user
        # mashes through stations faster than HTTP connects complete.
        self._opener_gen: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_airplay(self, controller: AirPlayController | None) -> None:
        """Swap the AirPlay routing target — `None` means 'play locally'.

        Stops any in-flight playback so the next `play()` starts cleanly on
        the new output. The TUI picker uses this to switch between local
        speakers and a discovered AirPlay device without restarting.
        """
        self.stop()
        self._airplay = controller

    def set_replaygain_mode(self, mode: ReplayGainMode) -> None:
        """Change ReplayGain normalisation mode for subsequent tracks."""
        self._replaygain_mode = mode

    def play(self, source: Path | str, *, replaygain: dict[str, str] | None = None) -> None:
        """Schedule playback of `source`. Returns immediately.

        The slow part of starting playback for live streams is the HTTP
        connect inside `av.open` — easily 1+ seconds. We do that on a
        background thread so:
          1. The UI doesn't freeze during the connect.
          2. The PREVIOUS source keeps playing right up until the new one
             is ready (no audible silence-then-pop on station switches).

        `source` can be a local `Path` or a URL string. Multiple rapid
        `play()` calls are handled via a generation counter — only the
        latest one's opener is allowed to swap.

        AirPlay routing: when an AirPlay device is connected AND `source`
        is a URL (radio stream or `/rest/stream` from a Subsonic server),
        hand the URL to the device instead of decoding locally. Local
        Path sources fall through to the normal in-process player —
        AirPlay-from-local would need a tiny inline HTTP server, which
        is deferred.

        `replaygain` is the per-track tag dict (typically
        `LibraryTrack.replaygain`). Resolved via the current mode into a
        scalar multiplier applied in the audio callback. AirPlay paths
        ignore this — the device handles its own gain.
        """
        # Compute up front so the audio callback can read it post-setup
        # without touching tag data on a worker thread.
        self._replaygain_multiplier = compute_replaygain_multiplier(
            replaygain or {},
            self._replaygain_mode,
        )
        if self._airplay is not None and self._airplay.device is not None and isinstance(source, str):
            self._opener_gen += 1
            self._teardown_playback()
            self._current_source = source
            self._is_live = source.startswith(("http://", "https://"))
            try:
                self._airplay.play_url(source)
            except Exception as exc:
                log.warning("AirPlay play_url failed for %s: %s", source, exc)
                if self.on_track_failed is not None:
                    self.on_track_failed(source, f"airplay: {exc}")
                return
            # `_teardown_playback` left `_stopped = True`; flip it back so
            # `is_playing` reports correctly while AirPlay is the output.
            with self._lock:
                self._stopped = False
                self._paused = False
            return

        self._opener_gen += 1
        gen = self._opener_gen
        threading.Thread(
            target=self._open_and_swap,
            args=(source, gen),
            name=f"musickit-opener-{Path(str(source)).name}",
            daemon=True,
        ).start()

    def _open_and_swap(self, source: Path | str, gen: int) -> None:
        """Worker thread: connect to `source`, then atomically take over playback."""
        try:
            container, stream = open_container(source)
        except Exception as exc:
            log.warning("failed to open %s: %s", source, exc)
            if gen == self._opener_gen and self.on_track_failed is not None:
                self.on_track_failed(source, str(exc))
            return
        if gen != self._opener_gen:
            # Either a newer play() OR a stop() superseded us while we were
            # connecting — discard this open without touching playback.
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            return
        # Tear the OLD playback down only now that the NEW container is
        # ready. Keeps the previous track audible during the HTTP connect
        # for stream-to-stream switches. Use the internal teardown so we
        # don't bump _opener_gen and invalidate ourselves.
        self._teardown_playback()
        if gen != self._opener_gen:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            return
        self._setup_playback(source, container, stream)

    def _setup_playback(self, source: Path | str, container: InputContainer, stream: Any) -> None:
        """Wire up state + threads for an already-opened container."""
        self._frames_played = 0
        self._end_fired = False
        self._paused = False
        self._stopped = False
        self._current_source = source
        # Per-decoder queue + stop event. The decoder thread captures these
        # references; even if the previous decoder didn't join in time, it
        # writes to its OWN orphaned queue and observes its OWN set stop
        # event, never touching this new playback's state.
        local_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        local_stop = threading.Event()
        self._queue = local_queue
        self._decoder_stop = local_stop
        self._current_chunk = None
        self._chunk_offset = 0

        self._duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
        self._is_live = self._duration <= 0
        self._stream_station_name = get_metadata_value(container, "icy-name")
        self._stream_title = get_metadata_value(container, "StreamTitle")
        if self.on_metadata_change is not None:
            try:
                self.on_metadata_change()
            except Exception:  # pragma: no cover
                pass

        thread_name = f"musickit-decoder-{Path(str(source)).name}"
        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            args=(container, stream, local_queue, local_stop, source),
            name=thread_name,
            daemon=True,
        )
        self._decoder_thread.start()

        # Prebuffer: don't start the output stream until the decoder has
        # produced at least a few chunks. Without this the first ~2 audio
        # callbacks fire while the queue is still empty, write silence, and
        # the user hears a "silence-then-pop" attack on every track. We
        # cap the wait so a stuck open / slow stream doesn't hang the UI.
        prebuffer_deadline = time.time() + _PREBUFFER_TIMEOUT_S
        while time.time() < prebuffer_deadline and local_queue.qsize() < _PREBUFFER_CHUNKS and not local_stop.is_set():
            time.sleep(0.01)

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
                self.on_track_failed(source, f"audio device unavailable: {exc}")

    def stop(self) -> None:
        """Stop playback, join the decoder thread, AND invalidate any pending opener.

        Bumping `_opener_gen` here ensures that `play(slow_url); stop()` doesn't
        let the slow opener finish and start playback after the user asked us
        to stop. `_open_and_swap` calls `_teardown_playback()` directly during
        track transitions so it doesn't invalidate itself.
        """
        self._opener_gen += 1
        self._teardown_playback()
        if self._airplay is not None and self._airplay.device is not None:
            try:
                self._airplay.stop()
            except Exception:  # pragma: no cover — best-effort
                pass

    def _teardown_playback(self) -> None:
        """Tear down the audio output stream + decoder thread + queue.

        Internal helper — does NOT bump `_opener_gen`. Used by both public
        `stop()` and `_open_and_swap` to swap in a new container.
        """
        with self._lock:
            self._stopped = True
            self._band_levels = [0.0] * _VIS_BANDS
        # Signal the OLD decoder via its captured stop event. Even if it
        # doesn't exit before the next _setup_playback installs new state,
        # it still observes its own set event and bails — never writes to
        # the new playback's queue.
        if self._decoder_stop is not None:
            self._decoder_stop.set()
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
        self._decoder_stop = None
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
        return self._current_source if isinstance(self._current_source, Path) else None

    @property
    def current_source(self) -> Path | str | None:
        return self._current_source

    @property
    def is_live(self) -> bool:
        """True when playing an unbounded stream (live radio / Icecast)."""
        return self._is_live

    @property
    def stream_title(self) -> str | None:
        """ICY `StreamTitle` (current track) — only meaningful while `is_live`."""
        return self._stream_title

    @property
    def stream_station_name(self) -> str | None:
        """ICY `icy-name` (station name) — set on stream open."""
        return self._stream_station_name

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

    def _decoder_loop(
        self,
        container: InputContainer,
        stream: Any,
        local_queue: queue.Queue[np.ndarray | None],
        local_stop: threading.Event,
        source: Path | str,
    ) -> None:
        """Decode `container` into `local_queue` until `local_stop` fires.

        All shared state is captured by parameter — no `self._queue` /
        `self._stopped` reads in the body. Stale decoders write to their
        own queue, see their own set stop event, and exit cleanly.
        """
        try:
            resampler = av.AudioResampler(format="flt", layout="stereo", rate=self._sample_rate)
            self._decode_into_queue(container, stream, resampler, local_queue, local_stop)
        except Exception as exc:  # pragma: no cover — surface decode errors softly
            log.warning("decoder failed for %s: %s", source, exc)
            if not local_stop.is_set() and self.on_track_failed is not None:
                self.on_track_failed(source, str(exc))
        finally:
            try:
                container.close()
            except Exception:  # pragma: no cover
                pass
            # Sentinel: tells the audio callback no more chunks are coming.
            # Always pushed to the LOCAL queue — never the new playback's.
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
    ) -> None:
        for packet in container.demux(stream):
            if local_stop.is_set():
                return
            # Honour seeks issued from the caller thread.
            target = self._consume_seek_target()
            if target is not None:
                self._apply_seek(container, stream, target, local_queue)
                continue
            # ICY metadata refresh: when streaming, `container.metadata` is
            # updated in-place by libavformat as `StreamTitle` lines arrive
            # in the stream. Polling per-packet is cheap.
            if self._is_live:
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
            if self.on_metadata_change is not None:
                try:
                    self.on_metadata_change()
                except Exception:  # pragma: no cover
                    pass

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
        # Drop queued PCM on the LOCAL queue so the next callback hears the
        # new position immediately. Never touches a different decoder's queue.
        while True:
            try:
                local_queue.get_nowait()
            except queue.Empty:
                break
        target_pts = int(seconds / float(stream.time_base))
        try:
            container.seek(target_pts, stream=stream)
        except av.error.FFmpegError:  # pragma: no cover
            return
        self._frames_played = int(seconds * self._sample_rate)

    def _push_frame(
        self,
        frame: AudioFrame,
        local_queue: queue.Queue[np.ndarray | None],
        local_stop: threading.Event,
    ) -> None:
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
        while offset < total and not local_stop.is_set():
            chunk = interleaved[offset : offset + _CHUNK_FRAMES]
            offset += chunk.shape[0]
            try:
                local_queue.put(np.ascontiguousarray(chunk, dtype=np.float32), timeout=1.0)
            except queue.Full:  # pragma: no cover
                if local_stop.is_set():
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
        # ReplayGain is applied as an additional multiplier alongside the
        # user volume slider. Computed in `play()` from the track's tags;
        # 1.0 when RG is off, untagged, or AirPlay-routed.
        gain = volume * self._replaygain_multiplier
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
            outdata[written : written + take] = chunk[self._chunk_offset : self._chunk_offset + take] * gain
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
