// musickit web UI — Web Audio + Canvas FFT visualizer.
//
// Loaded after app.js. Self-contained — only touches the global <audio>
// element + its own canvas. Toggled via `f` keybind or the visualizer
// button in the now-playing footer.
//
// Architecture:
//   - Lazy-init: create AudioContext + MediaElementAudioSourceNode +
//     AnalyserNode on the FIRST toggle. createMediaElementSource can
//     only be called once per <audio> element, so we cache the wired
//     graph and reuse it forever after.
//   - Render loop runs only while the canvas is visible. Toggling off
//     cancels the rAF; toggling back on restarts it. The AudioContext
//     keeps running either way (Chrome resumes it on the user click).
//   - 48 log-spaced bands from 30Hz to 16kHz. Each band averages the
//     FFT bins that fall in its [lo, hi) range. dB → linear via
//     `(db + 90) / 90`, clipped to [0, 1].
//   - Visual decay: each frame we lerp toward the target level — fast
//     attack, slow release — so loud transients pop while sustained
//     tones don't shimmer at 60fps.
//   - Color gradient: top of the bar is red, middle yellow, bottom
//     green — same as the TUI.

(function () {
  "use strict";

  const N_BANDS = 48;
  const FREQ_LO = 30;
  const FREQ_HI = 16000;
  const FFT_SIZE = 2048;
  const ATTACK = 0.45; // weight on the new value when level rises
  const RELEASE = 0.12; // weight on the new value when level falls
  const PAUSE_DECAY = 0.92; // per-frame multiplier when nothing's playing
  const DB_FLOOR = -90;
  const DB_RANGE = 90;

  const audio = document.getElementById("audio");
  const canvas = document.getElementById("viz-canvas");
  const toggleButton = document.querySelector('[data-action="toggle-viz"]');
  if (!audio || !canvas || !toggleButton) return;

  const ctx2d = canvas.getContext("2d");
  let audioCtx = null;
  let analyser = null;
  let freqData = null;
  let bandTargets = new Float32Array(N_BANDS);
  let bandLevels = new Float32Array(N_BANDS);
  let bandLoBin = null;
  let bandHiBin = null;
  let rafId = null;

  function ensureAudioGraph() {
    if (audioCtx) return;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      console.warn("Web Audio API not supported; visualizer disabled.");
      return;
    }
    audioCtx = new Ctor();
    const source = audioCtx.createMediaElementSource(audio);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = FFT_SIZE;
    analyser.smoothingTimeConstant = 0.0; // we apply our own attack/release
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
    freqData = new Float32Array(analyser.frequencyBinCount);
    computeBandRanges(audioCtx.sampleRate);
  }

  function computeBandRanges(sampleRate) {
    // FFT bin i covers frequency `i * sampleRate / fftSize`. Build per-band
    // [lo, hi] bin indices in log-spaced frequency.
    bandLoBin = new Int32Array(N_BANDS);
    bandHiBin = new Int32Array(N_BANDS);
    const binHz = sampleRate / FFT_SIZE;
    const logLo = Math.log(FREQ_LO);
    const logHi = Math.log(FREQ_HI);
    for (let i = 0; i < N_BANDS; i++) {
      const fLo = Math.exp(logLo + (logHi - logLo) * (i / N_BANDS));
      const fHi = Math.exp(logLo + (logHi - logLo) * ((i + 1) / N_BANDS));
      const loBin = Math.max(1, Math.floor(fLo / binHz));
      const hiBin = Math.max(loBin + 1, Math.ceil(fHi / binHz));
      bandLoBin[i] = loBin;
      bandHiBin[i] = Math.min(hiBin, freqData.length);
    }
  }

  function readBands() {
    if (!analyser) return;
    analyser.getFloatFrequencyData(freqData);
    for (let i = 0; i < N_BANDS; i++) {
      let sum = 0;
      let count = 0;
      const lo = bandLoBin[i];
      const hi = bandHiBin[i];
      for (let b = lo; b < hi; b++) {
        // freqData[b] is in dB, range roughly [-100, 0].
        sum += freqData[b];
        count++;
      }
      const avgDb = count > 0 ? sum / count : DB_FLOOR;
      const level = Math.max(0, Math.min(1, (avgDb - DB_FLOOR) / DB_RANGE));
      bandTargets[i] = level;
    }
  }

  function applyDecay() {
    const playing = !audio.paused && !audio.ended && state_isAudible();
    for (let i = 0; i < N_BANDS; i++) {
      if (playing) {
        const target = bandTargets[i];
        const cur = bandLevels[i];
        const w = target > cur ? ATTACK : RELEASE;
        bandLevels[i] = cur + (target - cur) * w;
        if (bandLevels[i] < 0.001) bandLevels[i] = 0;
      } else {
        bandLevels[i] *= PAUSE_DECAY;
        if (bandLevels[i] < 0.001) bandLevels[i] = 0;
      }
    }
  }

  // Cheap "are we actually decoding audio" probe — `audio.paused` is
  // false during a buffering pause too. We don't have a great signal
  // for "PortAudio just consumed bytes" in the browser, so this stays
  // as a paused-flag check; works fine for the common case.
  function state_isAudible() {
    return !audio.paused;
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    ctx2d.clearRect(0, 0, w, h);

    const gap = 2;
    const totalGaps = (N_BANDS - 1) * gap;
    const barWidth = Math.max(1, (w - totalGaps) / N_BANDS);

    for (let i = 0; i < N_BANDS; i++) {
      const level = bandLevels[i];
      if (level <= 0) continue;
      const barHeight = level * h;
      const x = i * (barWidth + gap);
      const y = h - barHeight;

      // Vertical gradient per-bar so each band fades from green (bottom)
      // through yellow (mid) to red (peak) — VU style. Building one
      // gradient per bar is expensive at 60fps, so we cache by quantized
      // height bucket; small enough that the cache is microseconds and
      // big enough that visual banding is invisible.
      const grad = gradientForHeight(barHeight, h);
      ctx2d.fillStyle = grad;
      ctx2d.fillRect(x, y, barWidth, barHeight);
    }
  }

  // Cheap per-height gradient cache. Keyed on the bucket so frame-to-frame
  // small-amplitude wiggle doesn't churn through createLinearGradient.
  const gradCache = new Map();
  function gradientForHeight(barHeight, fullHeight) {
    const bucket = Math.round((barHeight / fullHeight) * 64);
    let g = gradCache.get(bucket);
    if (!g) {
      g = ctx2d.createLinearGradient(0, fullHeight - barHeight, 0, fullHeight);
      g.addColorStop(0, "#f7768e");   // peak (red)
      g.addColorStop(0.45, "#e0af68"); // mid (amber)
      g.addColorStop(1, "#9ece6a");   // base (green)
      gradCache.set(bucket, g);
    }
    return g;
  }

  function loop() {
    readBands();
    applyDecay();
    draw();
    rafId = requestAnimationFrame(loop);
  }

  function show() {
    ensureAudioGraph();
    if (!analyser) return; // Web Audio unavailable
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    document.body.classList.add("is-viz");
    resize();
    if (rafId === null) {
      rafId = requestAnimationFrame(loop);
    }
  }

  function hide() {
    document.body.classList.remove("is-viz");
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    // Clear so a re-show doesn't flash the last frame.
    ctx2d.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  }

  function toggle() {
    if (document.body.classList.contains("is-viz")) hide();
    else show();
  }

  toggleButton.addEventListener("click", toggle);

  document.addEventListener("keydown", function (event) {
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      toggle();
    } else if (event.key === "Escape" && document.body.classList.contains("is-viz")) {
      hide();
    }
  });

  window.addEventListener("resize", function () {
    if (document.body.classList.contains("is-viz")) {
      resize();
    }
  });
})();
