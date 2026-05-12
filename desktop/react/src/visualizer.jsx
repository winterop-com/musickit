// Canvas-driven FFT visualizer. We don't have a real audio source in the
// prototype, but the brief requires this to be Canvas-driven; this synthesises
// a band-shaped spectrum that *moves* like a real FFT (low-freq dominant,
// noise-modulated peaks, smoothed falloff) so it reads as believable.
//
// Styles supported:
//   "bars"      classic vertical bars (current MusicKit look)
//   "mirror"    bars mirrored above/below a centerline
//   "radial"    polar bars radiating outward
//   "ambient"   low-contrast blurred wash (sits behind content)

const { useEffect: useEff_viz, useRef: useRef_viz } = React;

function Visualizer({ style = "bars", running = true, accent, height, ambient = false, dense = true }) {
  const canvasRef = useRef_viz(null);
  const stateRef = useRef_viz({ bins: null, raf: 0, t: 0 });

  useEff_viz(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;

    const N_BINS = dense ? 48 : 32;
    if (!stateRef.current.bins || stateRef.current.bins.length !== N_BINS) {
      stateRef.current.bins = new Float32Array(N_BINS);
    }
    const bins = stateRef.current.bins;

    function resize() {
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(r.width * dpr));
      canvas.height = Math.max(1, Math.floor(r.height * dpr));
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    function step() {
      stateRef.current.t += 1;
      const t = stateRef.current.t;

      // synthesise a "spectrum": low-freq dominant envelope * noise * slow LFO
      for (let i = 0; i < N_BINS; i++) {
        // 0..1 across bins; low i = bass.
        const x = i / N_BINS;
        // low-pass envelope (more energy in lows)
        const env = Math.pow(1 - x, 1.25) * 0.85 + 0.15;
        // slow modulation per bin
        const lfo = 0.55 + 0.45 * Math.sin(t * 0.04 + i * 0.6);
        // fast jitter
        const jitter = 0.7 + 0.3 * Math.sin(t * (0.11 + i * 0.013) + i);
        // occasional peak
        const peak = Math.random() < 0.02 ? 1.4 : 1;
        let target = env * lfo * jitter * peak;
        if (!running) target = bins[i] * 0.92; // decay when paused
        // ease toward target (attack fast, release slow)
        const cur = bins[i];
        const k = target > cur ? 0.45 : 0.12;
        bins[i] = cur + (target - cur) * k;
      }

      // draw
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      if (style === "radial") drawRadial(ctx, bins, w, h, accent, running, ambient);
      else if (style === "mirror") drawMirror(ctx, bins, w, h, accent, running, ambient);
      else if (style === "ambient") drawAmbient(ctx, bins, w, h, accent);
      else drawBars(ctx, bins, w, h, accent, running, ambient);

      stateRef.current.raf = requestAnimationFrame(step);
    }
    stateRef.current.raf = requestAnimationFrame(step);

    return () => {
      cancelAnimationFrame(stateRef.current.raf);
      ro.disconnect();
    };
  }, [style, running, accent, dense, ambient]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: "100%", height: height ? `${height}px` : "100%", display: "block" }}
    />
  );
}

function gradFor(ctx, x0, y0, x1, y1, accent) {
  const g = ctx.createLinearGradient(x0, y0, x1, y1);
  // green -> amber -> rose top-down (matches the current MK look)
  g.addColorStop(0, accent.hi || "#f08aa6");
  g.addColorStop(0.55, accent.mid || "#e6c065");
  g.addColorStop(1, accent.lo || "#bcd47a");
  return g;
}

function drawBars(ctx, bins, w, h, accent, running, ambient) {
  const N = bins.length;
  const gap = Math.max(2, Math.floor(w / N * 0.18));
  const bw = (w - gap * (N - 1)) / N;
  const baseAlpha = ambient ? 0.45 : 1;
  for (let i = 0; i < N; i++) {
    const v = Math.min(1, bins[i]);
    const bh = v * (h - 8);
    const x = i * (bw + gap);
    const y = h - bh;
    ctx.fillStyle = gradFor(ctx, x, y, x, h, accent);
    ctx.globalAlpha = baseAlpha * (running ? 1 : 0.7);
    roundRect(ctx, x, y, bw, bh, Math.min(bw / 3, 4));
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawMirror(ctx, bins, w, h, accent, running, ambient) {
  const N = bins.length;
  const gap = Math.max(2, Math.floor(w / N * 0.18));
  const bw = (w - gap * (N - 1)) / N;
  const cy = h / 2;
  const baseAlpha = ambient ? 0.4 : 1;
  for (let i = 0; i < N; i++) {
    const v = Math.min(1, bins[i]);
    const bh = v * (h / 2 - 4);
    const x = i * (bw + gap);
    ctx.globalAlpha = baseAlpha * (running ? 1 : 0.7);
    ctx.fillStyle = gradFor(ctx, x, cy - bh, x, cy + bh, accent);
    roundRect(ctx, x, cy - bh, bw, bh * 2, Math.min(bw / 3, 4));
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawRadial(ctx, bins, w, h, accent, running, ambient) {
  const N = bins.length;
  const cx = w / 2, cy = h / 2;
  const r0 = Math.min(w, h) * 0.18;
  const r1 = Math.min(w, h) * 0.48;
  const span = r1 - r0;
  const baseAlpha = ambient ? 0.4 : 1;
  // inner ring outline
  ctx.strokeStyle = accent.ring || "rgba(255,255,255,0.06)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.arc(cx, cy, r0 - 2, 0, Math.PI * 2); ctx.stroke();
  for (let i = 0; i < N; i++) {
    const v = Math.min(1, bins[i]);
    const a0 = (i / N) * Math.PI * 2;
    const a1 = ((i + 0.7) / N) * Math.PI * 2;
    const rr = r0 + v * span;
    ctx.globalAlpha = baseAlpha * (running ? 1 : 0.7);
    const g = ctx.createRadialGradient(cx, cy, r0, cx, cy, r1);
    g.addColorStop(0, accent.lo || "#bcd47a");
    g.addColorStop(0.6, accent.mid || "#e6c065");
    g.addColorStop(1, accent.hi || "#f08aa6");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, r0, a0, a1);
    ctx.arc(cx, cy, rr, a1, a0, true);
    ctx.closePath();
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawAmbient(ctx, bins, w, h, accent) {
  // Big blurred peaks behind content. Used as background fill.
  const N = bins.length;
  const cy = h * 0.55;
  ctx.globalAlpha = 0.45;
  for (let i = 0; i < N; i++) {
    const v = Math.min(1, bins[i]);
    const x = (i / (N - 1)) * w;
    const r = 30 + v * Math.min(w, h) * 0.35;
    const g = ctx.createRadialGradient(x, cy, 0, x, cy, r);
    const hue = i / N;
    const c = hue < 0.5 ? (accent.lo || "#bcd47a") : (accent.hi || "#f08aa6");
    g.addColorStop(0, c);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(x - r, cy - r, r * 2, r * 2);
  }
  ctx.globalAlpha = 1;
}

function roundRect(ctx, x, y, w, h, r) {
  if (h < r * 2) r = h / 2;
  if (w < r * 2) r = w / 2;
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

window.MK_Visualizer = Visualizer;
