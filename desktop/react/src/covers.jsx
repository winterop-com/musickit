// Album cover SVGs (procedurally generated — no asset dependency).
// Each cover is a 256x256 SVG returned as a data URL.

function makeCover(kind, baseColor) {
  // WIRED: if the caller passed a real cover-art URL (the wiring layer
  // sets `al.cover` to the Subsonic /rest/getCoverArt URL), bypass the
  // procedural generator and let <img src=...> hit the network.
  if (typeof kind === "string" && /^https?:\/\//.test(kind)) {
    return kind;
  }
  let inner = "";
  const c = baseColor || "#444";
  const c2 = shade(baseColor, -25);
  const c3 = shade(baseColor, 25);
  switch (kind) {
    case "enya":
      inner = `
        <rect width="256" height="256" fill="${c2}"/>
        <circle cx="128" cy="148" r="78" fill="${c}"/>
        <path d="M70 148 Q128 70 186 148 Q128 226 70 148Z" fill="${c3}" opacity="0.45"/>
        <text x="20" y="40" font-family="serif" font-style="italic" fill="#f3d5a8" font-size="24">enya</text>`;
      break;
    case "watermark":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <path d="M0 180 Q64 140 128 180 T256 180 L256 256 L0 256Z" fill="${c3}" opacity="0.6"/>
        <path d="M0 200 Q64 160 128 200 T256 200 L256 256 L0 256Z" fill="${c2}" opacity="0.8"/>`;
      break;
    case "shep":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <circle cx="180" cy="80" r="32" fill="#e8d99c" opacity="0.85"/>
        <circle cx="180" cy="80" r="20" fill="${c2}"/>`;
      break;
    case "depression":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <rect x="8" y="8" width="240" height="240" fill="none" stroke="#f0c0c0" stroke-width="2" opacity="0.5"/>
        <text x="128" y="140" text-anchor="middle" font-family="serif" font-style="italic" font-size="22" fill="#f0c0c0">depression</text>
        <text x="128" y="168" text-anchor="middle" font-family="serif" font-style="italic" font-size="22" fill="#f0c0c0">cherry</text>`;
      break;
    case "teendream":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <circle cx="128" cy="128" r="80" fill="${c3}" opacity="0.4"/>
        <circle cx="128" cy="128" r="50" fill="${c2}" opacity="0.5"/>`;
      break;
    case "saw2":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <g stroke="#5a5a5a" stroke-width="1" fill="none">
          <circle cx="80" cy="80" r="22"/>
          <circle cx="80" cy="80" r="36"/>
          <circle cx="180" cy="170" r="30"/>
          <circle cx="180" cy="170" r="48"/>
        </g>`;
      break;
    case "syro":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <text x="128" y="148" text-anchor="middle" font-family="monospace" font-size="58" font-weight="900" fill="#0a0a0a">syro</text>`;
      break;
    case "takk":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <path d="M0 256 L80 110 L130 170 L180 100 L256 256Z" fill="#ffffff" opacity="0.45"/>
        <path d="M0 256 L70 160 L120 200 L256 256Z" fill="#ffffff" opacity="0.7"/>`;
      break;
    case "airports":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <line x1="0" y1="190" x2="256" y2="190" stroke="#3a3a2a" stroke-width="1"/>
        <line x1="0" y1="200" x2="256" y2="200" stroke="#3a3a2a" stroke-width="1"/>
        <text x="22" y="40" font-family="sans-serif" font-size="14" fill="#3a3a2a" letter-spacing="3">AMBIENT 1</text>
        <text x="22" y="60" font-family="sans-serif" font-size="9" fill="#3a3a2a" letter-spacing="2">MUSIC FOR AIRPORTS</text>`;
      break;
    case "mhtrtc":
      inner = `
        <rect width="256" height="256" fill="${c}"/>
        <rect x="40" y="80" width="176" height="100" fill="#f0d7a8" opacity="0.7"/>
        <circle cx="100" cy="125" r="14" fill="${c2}"/>
        <circle cx="156" cy="125" r="14" fill="${c2}"/>
        <path d="M80 160 Q128 178 176 160" stroke="${c2}" stroke-width="3" fill="none"/>`;
      break;
    default:
      inner = `<rect width="256" height="256" fill="${c}"/>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">${inner}</svg>`;
  return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
}

function shade(hex, pct) {
  if (!hex || !hex.startsWith("#")) return hex || "#444";
  const num = parseInt(hex.slice(1), 16);
  let r = (num >> 16) & 255, g = (num >> 8) & 255, b = num & 255;
  const t = pct < 0 ? 0 : 255;
  const p = Math.abs(pct) / 100;
  r = Math.round((t - r) * p + r);
  g = Math.round((t - g) * p + g);
  b = Math.round((t - b) * p + b);
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

window.MK_makeCover = makeCover;
