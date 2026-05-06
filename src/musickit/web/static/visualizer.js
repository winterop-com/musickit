// musickit web UI — Web Audio + Canvas FFT visualizer.
//
// Always-visible small canvas above the now-playing footer (~64px).
// Press `f` (or click the ▦ button) to expand to fullscreen, replacing
// the three-pane browse area; press `f` again or Esc to shrink.
//
// Architecture:
//   - Lazy-init AudioContext + MediaElementAudioSourceNode +
//     AnalyserNode on the FIRST `audio.play` event. createMediaElement
//     Source can only be called once per <audio> element, and Chrome
//     blocks AudioContext until a user gesture — playing a track is
//     itself the user gesture, so this works without extra prompting.
//   - Once the graph exists, the rAF loop runs continuously: while
//     audio plays, bars track the FFT; while paused, bars decay
//     smoothly to zero. Toggling fullscreen just resizes the canvas.
//   - 48 log-spaced bands from 30Hz to 16kHz. Each band averages the
//     FFT bins that fall in its [lo, hi) range. dB → linear via
//     `(db + 90) / 90`, clipped to [0, 1].
//   - Asymmetric attack/release smoothing — fast attack so transients
//     pop, slow release so sustained tones don't shimmer at 60fps.

(function () {
  "use strict";

  const N_BANDS = 48;
  const FREQ_LO = 30;
  const FREQ_HI = 16000;
  const FFT_SIZE = 2048;
  const ATTACK = 0.45;
  const RELEASE = 0.12;
  const PAUSE_DECAY = 0.92;
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
  const bandTargets = new Float32Array(N_BANDS);
  const bandLevels = new Float32Array(N_BANDS);
  let bandLoBin = null;
  let bandHiBin = null;
  let rafId = null;

  function ensureAudioGraph() {
    if (audioCtx) return true;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      console.warn("[viz] Web Audio API not supported; visualizer disabled.");
      return false;
    }
    try {
      audioCtx = new Ctor();
      // Some browsers require explicit CORS opt-in even for same-origin
      // before MediaElementAudioSourceNode will pass actual samples to
      // an AnalyserNode (otherwise it returns silence). Set before we
      // wire the graph; same-origin requests just ignore it.
      if (!audio.crossOrigin) {
        audio.crossOrigin = "anonymous";
      }
      const source = audioCtx.createMediaElementSource(audio);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = FFT_SIZE;
      analyser.smoothingTimeConstant = 0.0; // we apply our own attack/release
      // Lower the dB floor so quiet signals still register on the bars.
      analyser.minDecibels = -90;
      analyser.maxDecibels = -10;
      source.connect(analyser);
      analyser.connect(audioCtx.destination);
      freqData = new Float32Array(analyser.frequencyBinCount);
      computeBandRanges(audioCtx.sampleRate);
      return true;
    } catch (err) {
      console.warn("[viz] init failed:", err);
      audioCtx = null;
      analyser = null;
      return false;
    }
  }

  function computeBandRanges(sampleRate) {
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
        sum += freqData[b];
        count++;
      }
      const avgDb = count > 0 ? sum / count : DB_FLOOR;
      const level = Math.max(0, Math.min(1, (avgDb - DB_FLOOR) / DB_RANGE));
      bandTargets[i] = level;
    }
  }

  function applyDecay() {
    const playing = !audio.paused && !audio.ended;
    let anyAudible = false;
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
      if (bandLevels[i] > 0) anyAudible = true;
    }
    // Hide the strip when there's nothing to show — paused AND every
    // band has decayed to zero. The fullscreen mode keeps the canvas
    // visible regardless (you opted into it explicitly).
    if (!playing && !anyAudible) {
      document.body.classList.add("viz-idle");
    } else {
      document.body.classList.remove("viz-idle");
    }
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
    if (w === 0 || h === 0) return; // not laid out yet
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
      const grad = gradientForHeight(barHeight, h);
      ctx2d.fillStyle = grad;
      ctx2d.fillRect(x, y, barWidth, barHeight);
    }
  }

  // Per-height gradient cache. Keyed on quantized height bucket so
  // small frame-to-frame amplitude wiggle doesn't churn through
  // createLinearGradient at 60fps. Cleared on resize.
  let gradCache = new Map();
  let gradCacheHeight = 0;

  function gradientForHeight(barHeight, fullHeight) {
    if (gradCacheHeight !== fullHeight) {
      gradCache = new Map();
      gradCacheHeight = fullHeight;
    }
    const bucket = Math.round((barHeight / fullHeight) * 64);
    let g = gradCache.get(bucket);
    if (!g) {
      g = ctx2d.createLinearGradient(0, fullHeight - barHeight, 0, fullHeight);
      g.addColorStop(0, "#f7768e"); // peak (red)
      g.addColorStop(0.45, "#e0af68"); // mid (amber)
      g.addColorStop(1, "#9ece6a"); // base (green)
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

  function startLoop() {
    if (rafId !== null) return;
    rafId = requestAnimationFrame(loop);
  }

  function toggleFullscreen() {
    document.body.classList.toggle("is-viz");
    // Layout changed — re-measure on the next frame so width/height
    // reflect the new size.
    requestAnimationFrame(resize);
  }

  // Lazy-init on first play. The autoplay policy in Chrome blocks
  // AudioContext until a user gesture; clicking a track to play
  // counts, so this is the right hook.
  audio.addEventListener("play", function () {
    if (!ensureAudioGraph()) return;
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    resize();
    startLoop();
  });

  toggleButton.addEventListener("click", toggleFullscreen);

  document.addEventListener("keydown", function (event) {
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      toggleFullscreen();
    } else if (event.key === "Escape" && document.body.classList.contains("is-viz")) {
      document.body.classList.remove("is-viz");
      requestAnimationFrame(resize);
    }
  });

  window.addEventListener("resize", function () {
    if (rafId !== null) resize();
  });

  // CSS-driven size changes (the height: 0 → 64px transition when
  // viz-idle clears, the flex: 1 in fullscreen mode) don't fire the
  // window resize event, so the canvas's backing store stays stuck
  // at the initial 0x0 measurement and bars draw into nothing. A
  // ResizeObserver on the canvas itself fires for those layout
  // changes and re-syncs the backing store to the new CSS box.
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(function () {
      resize();
    });
    ro.observe(canvas);
  }

  // First sizing pass on load — even before audio starts, so the
  // canvas backing store matches its CSS box.
  resize();

  // Start hidden — until the user plays something, there's nothing
  // to visualise. The decay loop will keep this class set during the
  // pause-decay window and re-add it once bars reach zero.
  document.body.classList.add("viz-idle");
})();
