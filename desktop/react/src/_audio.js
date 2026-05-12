// Audio controller — wraps a single <audio> element and exposes
// imperative play/pause/seek/volume/mute via `window.MK_AUDIO`.
//
// The Claude Designer artifact's `App()` keeps `playing`, `pos`, `vol`,
// `muted` as React state. Without a real audio element those just
// drive a setInterval that fakes a clock. The wiring layer needs to:
//
//   1. Point an <audio> at the stream URL when playTrack() runs
//   2. Forward UI controls (play/pause/seek/vol/mute) to that element
//   3. Push real `timeupdate` events back into React's `pos` state so
//      the scrub bar tracks the audio, not a synthetic clock
//   4. Fire `ended` so handleNext() runs at end-of-track
//
// Lives outside the artifact (underscored filename) so design-zip
// drops don't touch it.

(function () {
  "use strict";

  const audio = document.createElement("audio");
  audio.preload = "auto";
  audio.crossOrigin = "anonymous";
  // Attach to the document so DevTools / a11y trees see it. Hidden via
  // CSS — the artifact's transport bar IS the UI.
  audio.style.display = "none";
  document.documentElement.appendChild(audio);

  const listeners = {
    time: new Set(),
    ended: new Set(),
    durationchange: new Set(),
    error: new Set(),
  };

  audio.addEventListener("timeupdate", () => {
    for (const cb of listeners.time) cb(audio.currentTime);
  });
  audio.addEventListener("ended", () => {
    for (const cb of listeners.ended) cb();
  });
  audio.addEventListener("durationchange", () => {
    if (Number.isFinite(audio.duration)) {
      for (const cb of listeners.durationchange) cb(audio.duration);
    }
  });
  audio.addEventListener("error", () => {
    for (const cb of listeners.error) cb(audio.error);
  });

  window.MK_AUDIO = {
    load(url) {
      audio.src = url;
      audio.load();
    },
    async play() {
      try {
        await audio.play();
      } catch (err) {
        // Auto-play with sound is gated by user-gesture policy on first
        // load. The artifact only ever calls play() after a click, so
        // this should not fire in practice.
        console.warn("MK_AUDIO.play rejected:", err);
      }
    },
    pause() { audio.pause(); },
    seek(seconds) {
      if (!Number.isFinite(seconds)) return;
      // Don't crash on streams whose duration we don't know (radio).
      audio.currentTime = seconds;
    },
    setVolume(v) {
      audio.volume = Math.max(0, Math.min(1, v));
    },
    setMuted(b) { audio.muted = !!b; },
    onTimeUpdate(cb) { listeners.time.add(cb); return () => listeners.time.delete(cb); },
    onEnded(cb) { listeners.ended.add(cb); return () => listeners.ended.delete(cb); },
    onDurationChange(cb) { listeners.durationchange.add(cb); return () => listeners.durationchange.delete(cb); },
    onError(cb) { listeners.error.add(cb); return () => listeners.error.delete(cb); },
    get element() { return audio; },
  };
})();
