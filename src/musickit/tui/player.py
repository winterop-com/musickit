"""Public AudioPlayer — RPC client over the audio subprocess.

The actual decoding + audio output lives in `audio_engine` running in a
separate process. This module owns the public interface the UI calls
(`play`, `stop`, `toggle_pause`, `seek`, `set_volume`, `set_airplay`,
properties for position / duration / band levels / etc.) and forwards
everything to the engine via two `multiprocessing.Queue`s plus a
shared-memory block for high-frequency state.

Why a subprocess: the sounddevice audio callback is implemented in
Python and acquires the GIL on every fire. With the engine in its own
interpreter, UI work in the main process (Textual reflows, focus
changes, GC) can no longer stall the callback into a buffer underrun.

AirPlay is kept in this process for v1 — pyatv just sends URLs to the
device, no decoder/callback contention to worry about.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import threading
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING, Any, Callable

from musickit.tui.audio_engine import SharedState, engine_main
from musickit.tui.audio_proto import (
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
    from musickit.tui.airplay import AirPlayController

log = logging.getLogger(__name__)

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
    """RPC client for the audio engine subprocess.

    The public interface matches the previous in-process implementation
    so callers in `tui/app.py` need no changes. Internally every method
    pushes a `Command` onto the engine's queue (or writes shared
    memory) and the engine handles the rest in its own interpreter.

    Threading model from the UI's perspective:
      - Caller thread (Textual UI): public methods.
      - Event reader thread: drains the engine's event queue, fires
        the on_track_end / on_track_failed / on_metadata_change
        callbacks. Callbacks are invoked from THIS thread, same as
        before — UI code that needs main-thread updates uses
        `App.call_from_thread` (already does).
    """

    on_track_end: Callable[[], None] | None = None
    on_track_failed: Callable[[Path | str, str], None] | None = None
    on_metadata_change: Callable[[], None] | None = None

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        *,
        airplay: AirPlayController | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        # AirPlay routing stays in this process — pyatv just sends URLs to
        # the device, no decoder/callback work to worry about.
        self._airplay = airplay
        self._airplay_active: bool = False

        # Local cache of state the UI reads via properties — populated by
        # STARTED / METADATA_CHANGED events from the engine.
        self._current_source: Path | str | None = None
        self._is_live: bool = False
        self._stream_title: str | None = None
        self._stream_station_name: str | None = None
        self._replaygain_mode: ReplayGainMode = "auto"
        self._opener_gen = 0

        # Shared-memory state. mp.Value/Array provide an atomic-per-slot
        # interface and a built-in lock; no Python-level lock needed.
        ctx = mp.get_context("spawn")
        self._state = SharedState(
            position_frames=ctx.Value("q", 0),
            duration_s=ctx.Value("d", 0.0),
            paused=ctx.Value("b", 0),
            stopped=ctx.Value("b", 1),
            is_live=ctx.Value("b", 0),
            volume=ctx.Value("d", 1.0),
            replaygain_multiplier=ctx.Value("d", 1.0),
            band_levels=ctx.Array("d", [0.0] * VIS_BANDS),
        )
        self._cmd_queue: Any = ctx.Queue()
        self._event_queue: Any = ctx.Queue()

        # Spawn the engine. `daemon=True` so the parent's exit kills it.
        self._proc: Any = ctx.Process(
            target=engine_main,
            args=(self._cmd_queue, self._event_queue, self._state),
            name="musickit-audio-engine",
            daemon=True,
        )
        self._proc.start()

        # Reader thread: forwards engine events to UI callbacks.
        self._reader_stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_events,
            name="musickit-audio-reader",
            daemon=True,
        )
        self._reader.start()

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

        AirPlay routing: if an AirPlay device is connected AND `source` is
        a URL, hand the URL straight to pyatv. Local Path sources fall
        back to engine-side decoding.

        `replaygain` is the per-track tag dict; resolved into a multiplier
        on this side and pushed via shared memory before the engine starts
        the new track, so the audio callback can read it without IPC.
        """
        # Compute the RG multiplier here and push it into shared memory —
        # the engine's audio callback reads from `_state.replaygain_multiplier`.
        multiplier = compute_replaygain_multiplier(replaygain or {}, self._replaygain_mode)
        with self._state.replaygain_multiplier.get_lock():
            self._state.replaygain_multiplier.value = multiplier

        if self._airplay is not None and self._airplay.device is not None and isinstance(source, str):
            # Tear down any engine-side playback first; AirPlay is the new
            # output target.
            self._opener_gen += 1
            self._send(Op.STOP)
            self._current_source = source
            self._is_live = source.startswith(("http://", "https://"))
            try:
                self._airplay.play_url(source)
            except Exception as exc:
                log.warning("AirPlay play_url failed for %s: %s", source, exc)
                if self.on_track_failed is not None:
                    self.on_track_failed(source, f"airplay: {exc}")
                return
            with self._state.stopped.get_lock():
                self._state.stopped.value = 0
            with self._state.paused.get_lock():
                self._state.paused.value = 0
            self._airplay_active = True
            return

        # Local playback — hand off to the engine.
        self._opener_gen += 1
        self._airplay_active = False
        self._send(
            Op.PLAY,
            payload=PlayPayload(
                source=str(source),
                is_path=isinstance(source, Path),
                replaygain=dict(replaygain or {}),
            ),
            gen=self._opener_gen,
        )

    def stop(self) -> None:
        """Stop playback (engine-side and AirPlay-side)."""
        self._opener_gen += 1
        self._send(Op.STOP)
        if self._airplay is not None and self._airplay.device is not None:
            try:
                self._airplay.stop()
            except Exception:  # pragma: no cover
                pass
        self._airplay_active = False

    def toggle_pause(self) -> None:
        """Toggle pause state on the active output (engine OR AirPlay)."""
        if self._airplay_active and self._airplay is not None:
            new_paused = not self._is_paused_local()
            with self._state.paused.get_lock():
                self._state.paused.value = 1 if new_paused else 0
            try:
                if new_paused:
                    self._airplay.pause()
                else:
                    self._airplay.resume()
            except Exception:  # pragma: no cover
                log.warning("airplay pause/resume failed", exc_info=True)
            return
        self._send(Op.TOGGLE_PAUSE)

    def seek(self, seconds: float) -> None:
        """Seek to absolute position `seconds` from start."""
        self._send(Op.SEEK, payload=float(seconds))

    def set_volume(self, percent: int) -> None:
        clamped = max(0, min(100, percent))
        with self._state.volume.get_lock():
            self._state.volume.value = clamped / 100.0
        if self._airplay_active and self._airplay is not None:
            try:
                self._airplay.set_volume(clamped)
            except Exception:  # pragma: no cover
                log.warning("airplay set_volume failed", exc_info=True)

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        with self._state.position_frames.get_lock():
            frames = self._state.position_frames.value
        return frames / self._sample_rate

    @property
    def duration(self) -> float:
        with self._state.duration_s.get_lock():
            return self._state.duration_s.value

    @property
    def is_playing(self) -> bool:
        with self._state.stopped.get_lock():
            stopped = bool(self._state.stopped.value)
        return not stopped and not self._is_paused_local()

    @property
    def is_paused(self) -> bool:
        return self._is_paused_local()

    @property
    def volume(self) -> int:
        with self._state.volume.get_lock():
            return int(round(self._state.volume.value * 100))

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
        """ICY `StreamTitle` — only meaningful while `is_live`."""
        return self._stream_title

    @property
    def stream_station_name(self) -> str | None:
        """ICY `icy-name` — set on stream open."""
        return self._stream_station_name

    @property
    def band_levels(self) -> list[float]:
        """24 amplitude bins (0.0–1.0) for the spectrum visualizer."""
        with self._state.band_levels.get_lock():
            return [float(self._state.band_levels[i]) for i in range(VIS_BANDS)]

    def update_band_levels(self) -> None:
        """No-op — band levels are now updated by the engine and read via shared memory."""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_paused_local(self) -> bool:
        with self._state.paused.get_lock():
            return bool(self._state.paused.value)

    def _send(self, op: Op, *, payload: Any = None, gen: int = 0) -> None:
        try:
            self._cmd_queue.put(Command(op=op, payload=payload, gen=gen))
        except Exception:  # pragma: no cover — engine subprocess is gone
            log.warning("failed to send %s to audio engine", op)

    def _read_events(self) -> None:
        """Forward engine events to the UI's registered callbacks."""
        while not self._reader_stop.is_set():
            try:
                event: Event = self._event_queue.get(timeout=0.1)
            except Empty:
                continue
            except Exception:  # pragma: no cover — queue closed during shutdown
                return
            try:
                self._dispatch_event(event)
            except Exception:  # pragma: no cover — UI handler threw
                log.exception("audio event dispatch failed for %s", event.op)

    def _dispatch_event(self, event: Event) -> None:
        # Drop stale events. `event.gen` is the generation of the PLAY
        # that produced this event; we ignore anything older than the
        # latest gen we've sent. Critical for the local→AirPlay path
        # where the engine tears down its old playback but a delayed
        # TRACK_FAILED / STARTED could otherwise overwrite the AirPlay
        # state we just installed UI-side. `gen=0` is the sentinel for
        # "no playback ever started" — we only filter when gen > 0.
        if event.gen and event.gen < self._opener_gen:
            return
        if event.op is EventOp.STARTED:
            payload = event.payload
            assert isinstance(payload, StartedPayload)
            self._current_source = Path(payload.source) if payload.is_path else payload.source
            self._is_live = payload.is_live
            self._stream_title = payload.stream_title
            self._stream_station_name = payload.stream_station_name
            if self.on_metadata_change is not None:
                self.on_metadata_change()
        elif event.op is EventOp.TRACK_END:
            if self.on_track_end is not None:
                self.on_track_end()
        elif event.op is EventOp.TRACK_FAILED:
            payload = event.payload
            source = payload["source"]
            message = payload["message"]
            if self.on_track_failed is not None:
                self.on_track_failed(source, message)
        elif event.op is EventOp.METADATA_CHANGED:
            payload = event.payload
            self._stream_title = payload.get("stream_title")
            station = payload.get("stream_station_name")
            if station is not None:
                self._stream_station_name = station
            if self.on_metadata_change is not None:
                self.on_metadata_change()

    def shutdown(self) -> None:
        """Cleanly terminate the audio engine subprocess. Idempotent."""
        if self._reader_stop.is_set():
            return
        self._reader_stop.set()
        try:
            self._send(Op.SHUTDOWN)
        except Exception:  # pragma: no cover
            pass
        if self._proc is not None and self._proc.is_alive():
            self._proc.join(timeout=2.0)
            if self._proc.is_alive():  # pragma: no cover — engine hung
                self._proc.terminate()
                self._proc.join(timeout=1.0)

    def __del__(self) -> None:
        # Best-effort cleanup if the user forgot to call shutdown(). The
        # daemon=True flag on the process means it'll die with the parent
        # anyway, but explicit shutdown is cleaner.
        try:
            self.shutdown()
        except Exception:  # pragma: no cover
            pass
