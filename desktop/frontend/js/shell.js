// Main shell — runs after a successful login. Renders the three-pane
// browser (artists / albums / tracks) and the Now Playing card,
// streams audio via `<audio>` directly off /rest/stream.
//
// All data lives in-memory; no caching beyond the artist list, which
// we fetch once per session. Subsequent navigation is single-fetch
// per drill-in (getArtist, getAlbum). Cover-art URLs hit the server's
// own LRU cache (musickit serve) or built-in caching (Navidrome) so
// we don't add a second cache layer.

import { subsonicClient } from "./api.js";

// Lucide-sourced SVG icon set. Paths copied verbatim from lucide.dev so
// migrating to `lucide-react` once the desktop SPA moves to React is
// purely mechanical (`${ICONS.heart}` → `<Heart />`, same shape data,
// same `currentColor` theming). Sizes are controlled by font-size on
// the wrapper via `.icon svg { width:1em; height:1em }` — see shell.css.
const ICONS = {
  play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>`,
  pause: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="4" height="16" x="6" y="4"/><rect width="4" height="16" x="14" y="4"/></svg>`,
  skipBack: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/></svg>`,
  skipForward: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/></svg>`,
  heart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/></svg>`,
  heartFilled: `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.29 1.51 4.04 3 5.5l7 7Z"/></svg>`,
  radio: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg>`,
  volumeHigh: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/></svg>`,
  volumeLow: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/></svg>`,
  volumeMute: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><line x1="22" x2="16" y1="9" y2="15"/><line x1="16" x2="22" y1="9" y2="15"/></svg>`,
};

/** Mount the shell into `root`. Takes the SubsonicClient + session. */
export function renderShell(root, client, session, hooks = {}) {
  root.innerHTML = `
    <header class="topbar" data-tauri-drag-region>
      <div class="topbar-left" data-tauri-drag-region>
        <input type="search" id="search" class="search-input"
               placeholder="search…  (/)" autocomplete="off" spellcheck="false">
        <div class="search-dropdown" id="search-dropdown" hidden>
          <section class="search-section" data-kind="artist" hidden>
            <h3 class="search-section-title">Artists</h3>
            <ul class="search-section-list"></ul>
          </section>
          <section class="search-section" data-kind="album" hidden>
            <h3 class="search-section-title">Albums</h3>
            <ul class="search-section-list"></ul>
          </section>
          <section class="search-section" data-kind="song" hidden>
            <h3 class="search-section-title">Tracks</h3>
            <ul class="search-section-list"></ul>
          </section>
          <p class="search-empty" hidden>No matches.</p>
        </div>
      </div>
      <div class="topbar-center" data-tauri-drag-region>
        <span class="version" id="server-info" data-tauri-drag-region></span>
      </div>
      <div class="topbar-right" data-tauri-drag-region>
        <span class="user" id="topbar-user" data-tauri-drag-region></span>
        <button type="button" class="ghost-button" id="signout-btn">Sign out</button>
      </div>
    </header>

    <section class="now-playing-region" aria-label="Now playing">
      <section class="panel np-card" aria-label="Now Playing">
        <h2 class="panel-title">Now Playing</h2>
        <div class="np-card-body">
          <img id="np-cover" class="np-cover" alt=""
               src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>">
          <div class="np-meta">
            <span class="np-title" id="np-title">Nothing playing</span>
            <span class="np-artist-line">
              <span class="np-artist" id="np-artist">—</span>
              <span class="np-album-sep" id="np-album-sep"></span>
              <span class="np-album" id="np-album"></span>
            </span>
            <span class="np-extra-line">
              <span class="np-label">Year:</span> <span id="np-year">—</span>
              <span class="np-spacer">·</span>
              <span class="np-label">Format:</span> <span id="np-format">—</span>
            </span>
          </div>
          <div class="np-controls">
            <button type="button" class="np-button" data-action="prev"
                    id="prev-button" disabled aria-label="Previous track">
              <span aria-hidden="true">${ICONS.skipBack}</span>
            </button>
            <button type="button" class="play-button" data-action="play-pause"
                    id="play-button" disabled aria-label="Play / pause">
              <span aria-hidden="true">${ICONS.play}</span>
            </button>
            <button type="button" class="np-button" data-action="next"
                    id="next-button" disabled aria-label="Next track">
              <span aria-hidden="true">${ICONS.skipForward}</span>
            </button>
            <button type="button" class="np-button np-mute" id="mute-button" aria-label="Mute">
              <span aria-hidden="true" id="mute-glyph">${ICONS.volumeHigh}</span>
            </button>
            <input type="range" id="volume-slider" class="volume-slider"
                   min="0" max="1" step="0.01" value="1" aria-label="Volume">
          </div>
        </div>
        <div class="np-progress" id="np-progress">
          <span class="np-state-icon" id="np-state-icon">■</span>
          <span class="np-pos" id="np-pos">00:00</span>
          <progress id="np-bar" max="1" value="0"></progress>
          <span class="np-dur" id="np-dur">00:00</span>
        </div>
      </section>

      <section class="panel viz-panel" aria-label="Spectrum">
        <h2 class="panel-title">Spectrum</h2>
        <canvas class="visualizer-canvas" id="viz-canvas" aria-hidden="true"></canvas>
      </section>
    </section>

    <main class="three-pane">
      <section class="pane pane-sidebar" aria-label="Artists">
        <section class="panel" aria-label="Radio">
          <h2 class="panel-title">Radio</h2>
          <button type="button" class="row-button" id="radio-toggle">
            <span class="row-icon">${ICONS.radio}</span> Stations
          </button>
        </section>

        <section class="panel" aria-label="Starred">
          <h2 class="panel-title">Starred</h2>
          <button type="button" class="row-button" id="starred-toggle">
            <span class="row-icon">${ICONS.heart}</span> Tracks
          </button>
        </section>

        <section class="panel panel-grow" aria-label="Browse">
          <h2 class="panel-title">Artists <span class="count" id="artists-count"></span></h2>
          <ul class="row-list" id="artists-list">
            <li class="empty">Loading…</li>
          </ul>
        </section>
      </section>

      <section class="pane pane-albums" aria-label="Albums">
        <section class="panel panel-grow">
          <h2 class="panel-title" id="albums-title">Albums</h2>
          <div id="albums-pane">
            <p class="empty">Pick an artist to see albums.</p>
          </div>
        </section>
      </section>

      <section class="pane pane-tracks" aria-label="Tracks">
        <section class="panel panel-grow">
          <h2 class="panel-title" id="tracks-title">Tracks</h2>
          <div id="tracks-pane">
            <p class="empty">Pick an album to see tracks.</p>
          </div>
        </section>
      </section>
    </main>

    <audio id="audio" preload="none"></audio>
  `;

  // Runtime drag-handle wiring for Tauri.
  //
  // Tauri 2 exposes the current-window object via two parallel global
  // paths depending on the runtime config — `window.__TAURI__.window
  // .getCurrentWindow()` and `window.__TAURI__.webviewWindow
  // .getCurrentWebviewWindow()`. Both return an object with
  // `.startDragging()` available, but which one is populated depends
  // on plugin presence + Tauri-CLI version. Trying both keeps this
  // working across the version churn.
  //
  // Why a runtime listener instead of just CSS `-webkit-app-region:
  // drag` (which works for Electron)? Tauri 2's WKWebView ignores
  // that property entirely; the Tauri docs require either the
  // `data-tauri-drag-region` HTML attribute (also set, but Tauri
  // sometimes misses it on dynamically-rendered DOM) or the runtime
  // `startDragging()` API. Document-level delegation means it works
  // even if the topbar is later re-rendered.
  function getTauriWindow() {
    const t = /** @type {{ window?: any; webviewWindow?: any }} */ (window).__TAURI__;
    if (!t) return null;
    if (typeof t.window?.getCurrentWindow === "function") return t.window.getCurrentWindow();
    if (typeof t.webviewWindow?.getCurrentWebviewWindow === "function") {
      return t.webviewWindow.getCurrentWebviewWindow();
    }
    if (typeof t.window?.getCurrent === "function") return t.window.getCurrent();
    return null;
  }

  document.addEventListener("mousedown", (event) => {
    if (event.buttons !== 1) return;
    const target = /** @type {Element} */ (event.target);
    const topbar = target.closest?.(".topbar");
    if (!topbar) return;
    if (target.closest("input, button, a, .search-dropdown")) return;
    const tauriWin = getTauriWindow();
    if (!tauriWin?.startDragging) return;
    // Fire-and-forget — `await` would yield the event loop before
    // Tauri's native side could grab the OS mouse-event stream,
    // sometimes resulting in a no-op drag. Logging the rejection
    // (instead of swallowing it) so DevTools surfaces the cause if
    // something on the Rust side blocks the call.
    tauriWin.startDragging().catch((e) => console.warn("startDragging failed:", e));
  });

  // Wire up topbar identity / sign-out.
  document.getElementById("topbar-user").textContent = session.user;
  const serverLabel = session.server_type
    ? `${session.server_type}${session.server_version ? " v" + session.server_version : ""}`
    : "";
  document.getElementById("server-info").textContent = serverLabel;
  document.getElementById("signout-btn")?.addEventListener("click", () => hooks.onSignOut?.());

  const state = {
    queue: [], // [{id, title, artist, albumId, albumTitle, albumYear, rowEl, suffix}]
    queueIndex: -1,
    currentArtistId: null,
    currentAlbumId: null,
    currentTrackId: null,
  };

  // -----------------------------------------------------------------
  // Refresh-restore (URL hash + localStorage).
  //
  // Same pattern as the web UI's app.js: the URL hash mirrors the
  // navigation (`#a=&l=&t=`) so hitting Cmd+R lands the user back
  // where they were. localStorage holds the volume slider position
  // (TODO: + repeat/shuffle once we add those keybinds in Phase D).
  // -----------------------------------------------------------------

  const LS_VOLUME = "mk_desktop_volume";

  function updateHash() {
    const parts = [];
    if (state.currentArtistId) parts.push("a=" + encodeURIComponent(state.currentArtistId));
    if (state.currentAlbumId) parts.push("l=" + encodeURIComponent(state.currentAlbumId));
    if (state.currentTrackId) parts.push("t=" + encodeURIComponent(state.currentTrackId));
    const h = parts.length ? "#" + parts.join("&") : "";
    history.replaceState(null, "", window.location.pathname + h);
  }

  function parseHash() {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return {};
    const params = new URLSearchParams(hash);
    return {
      artistId: params.get("a"),
      albumId: params.get("l"),
      trackId: params.get("t"),
    };
  }

  const audio = document.getElementById("audio");
  const playButton = document.getElementById("play-button");
  const prevButton = document.getElementById("prev-button");
  const nextButton = document.getElementById("next-button");

  // Tag the audio element CORS-anonymous up-front so the visualizer's
  // first-play `audio.crossOrigin = "anonymous"` is a no-op (the
  // `if (!audio.crossOrigin)` check finds it set). Without this, the
  // visualizer's lazy-init RELOADS the element mid-play to apply CORS
  // — for radio streams that means ~1s of buffered audio plays from
  // the pre-CORS fetch, then the reload aborts and the second fetch's
  // audio never reaches the speakers. Setting it once at mount means
  // every fetch is CORS-tagged from the start, no reload happens.
  // Subsonic /rest/stream and our /rest/radioStream proxy both send
  // `Access-Control-Allow-Origin: *` so the request goes through.
  audio.crossOrigin = "anonymous";

  // -----------------------------------------------------------------
  // Boot: restore volume + artist list, then replay the URL hash.
  // -----------------------------------------------------------------
  try {
    const v = parseFloat(localStorage.getItem(LS_VOLUME));
    if (!Number.isNaN(v) && v >= 0 && v <= 1) audio.volume = v;
  } catch (e) {
    // localStorage unavailable.
  }

  loadArtists().then(() => restoreFromHash());

  /** Replay the artist → album → track drill-down from window.location.hash. */
  async function restoreFromHash() {
    const { artistId, albumId, trackId } = parseHash();
    if (!artistId) return;
    const artistBtn = document.querySelector(
      `[data-action], .row-button[data-artist-id="${cssEscape(artistId)}"]`,
    );
    // The artist buttons aren't tagged with data-action in shell.js;
    // we look up by the artist id we stamped on the button itself.
    const target = document.querySelector(
      `.pane-sidebar .row-button[data-artist-id="${cssEscape(artistId)}"]`,
    );
    if (!target) return;
    target.click();
    if (!albumId) return;
    const albumBtn = await waitForElement(
      `.pane-albums .row-button[data-album-id="${cssEscape(albumId)}"]`,
    );
    if (!albumBtn) return;
    albumBtn.click();
    if (!trackId) return;
    const trackBtn = await waitForElement(
      `.pane-tracks .row-button[data-track-id="${cssEscape(trackId)}"]`,
    );
    if (trackBtn) trackBtn.scrollIntoView({ block: "center" });
    // Don't auto-play — browser autoplay rules require a user gesture.
  }

  function waitForElement(selector, root = document, maxMs = 3000) {
    return new Promise((resolve) => {
      const start = Date.now();
      function tick() {
        const el = root.querySelector(selector);
        if (el) return resolve(el);
        if (Date.now() - start > maxMs) return resolve(null);
        setTimeout(tick, 40);
      }
      tick();
    });
  }

  function cssEscape(s) {
    return String(s).replace(/"/g, '\\"');
  }

  // -----------------------------------------------------------------
  // Radio mode — `getInternetRadioStations` + click-to-play.
  //
  // Spec endpoint, supported by every Subsonic-compatible server.
  // musickit serve returns the ~/.config/musickit/radio.toml list;
  // Navidrome returns whatever its admins configured. Both work the
  // same here because the response shape is canonical.
  // -----------------------------------------------------------------
  document.getElementById("radio-toggle")?.addEventListener("click", () => loadRadio());

  async function loadRadio() {
    const radioBtn = document.getElementById("radio-toggle");
    markActive(".pane-sidebar", radioBtn);
    state.currentArtistId = null;
    state.currentAlbumId = null;
    state.currentTrackId = null;
    updateHash();
    document.body.classList.add("is-radio");
    document.body.classList.remove("is-starred");

    const albumsTitle = document.getElementById("albums-title");
    if (albumsTitle) albumsTitle.textContent = "Stations";
    const pane = document.getElementById("albums-pane");
    pane.innerHTML = `<p class="empty">Loading stations…</p>`;
    document.getElementById("tracks-pane").innerHTML =
      `<p class="empty">Pick a station to start streaming.</p>`;
    try {
      const inner = await client.query("getInternetRadioStations");
      const stations = inner?.internetRadioStations?.internetRadioStation || [];
      if (stations.length === 0) {
        pane.innerHTML = `<p class="empty">No internet radio stations on this server.</p>`;
        return;
      }
      const list = document.createElement("ul");
      list.className = "row-list";
      for (const s of stations) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "row-button";
        button.dataset.streamUrl = s.streamUrl;
        button.dataset.stationName = s.name;
        button.innerHTML = `
          <span class="row-icon">${ICONS.radio}</span>
          <span class="album-text">
            <span class="album-title">${escapeHtml(s.name || "—")}</span>
            ${
              s.homepageUrl
                ? `<span class="album-meta"><span class="album-artist">${escapeHtml(s.homepageUrl)}</span></span>`
                : ""
            }
          </span>
        `;
        button.addEventListener("click", () => playStation(s, button));
        li.appendChild(button);
        list.appendChild(li);
      }
      pane.innerHTML = "";
      pane.appendChild(list);
    } catch (e) {
      pane.innerHTML = `<p class="empty">Failed: ${escapeHtml(e?.message || String(e))}</p>`;
    }
  }

  function playStation(s, button) {
    // Route radio through musickit serve's `/rest/radioStream` proxy
    // (Subsonic-auth'd) instead of the raw Icecast / SHOUTcast URL.
    // The visualizer leaves `audio.crossOrigin = "anonymous"` set so it
    // can read FFT samples; raw upstream radio servers almost never
    // return CORS headers, which would make the browser block the
    // cross-origin fetch and playback never starts. The server-side
    // proxy adds open CORS headers and parses inline ICY metadata.
    // Only the configured `radio.toml` stations are allowed through.
    audio.src = client.mediaUrl("radioStream", { url: s.streamUrl });
    audio.play().catch((err) => console.warn("station playback failed:", err));

    document.querySelectorAll(".row-button.is-playing").forEach((el) => {
      el.classList.remove("is-playing");
    });
    button.classList.add("is-playing");

    state.queue = [
      {
        id: "radio:" + s.streamUrl,
        title: s.name || "Radio",
        artist: "Radio",
        albumId: null,
        albumTitle: "",
        albumYear: "",
        suffix: "Stream",
        rowEl: button,
        kind: "radio",
        url: s.streamUrl,
      },
    ];
    state.queueIndex = 0;

    document.getElementById("np-title").textContent = s.name || "Radio";
    document.getElementById("np-artist").textContent = "Radio";
    const albumEl = document.getElementById("np-album");
    const sepEl = document.getElementById("np-album-sep");
    if (albumEl) albumEl.textContent = "";
    if (sepEl) sepEl.textContent = "";
    document.getElementById("np-year").textContent = "—";
    document.getElementById("np-format").textContent = "Stream";
    const cover = document.getElementById("np-cover");
    cover.src = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'/>";

    updateMediaSessionMetadata(state.queue[0]);

    playButton.disabled = false;
    prevButton.disabled = true;
    nextButton.disabled = true;
  }

  async function loadArtists() {
    try {
      const inner = await client.query("getArtists");
      // The Subsonic spec wraps artists in an `index` array per
      // alphabetical bucket. Flatten preserving order so the UI
      // alphabetises by appearance.
      const indexes = inner?.artists?.index || [];
      const artists = [];
      for (const idx of indexes) {
        const list = idx.artist || [];
        for (const a of list) artists.push(a);
      }
      const list = document.getElementById("artists-list");
      const count = document.getElementById("artists-count");
      if (count) count.textContent = `(${artists.length})`;
      list.innerHTML = "";
      if (artists.length === 0) {
        list.innerHTML = `<li class="empty">No artists indexed on this server.</li>`;
        return;
      }
      for (const a of artists) {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "row-button";
        btn.textContent = a.name;
        btn.dataset.artistId = a.id;
        btn.addEventListener("click", () => loadArtist(a.id, btn));
        li.appendChild(btn);
        list.appendChild(li);
      }
    } catch (e) {
      // The first API call after restoring a session is also our
      // implicit "is the server still reachable?" probe. If it fails
      // (network error, 401, server-not-Subsonic) the saved session
      // is unusable — kick the user back to the login screen with a
      // short banner so they can either retry or pick a different
      // server. Without this they'd be stuck staring at "Failed to
      // load artists" forever.
      const msg = e?.subsonicMessage || e?.message || String(e);
      const list = document.getElementById("artists-list");
      list.innerHTML = `<li class="empty">Server unreachable — signing out…<br><span class="dim">${escapeHtml(msg)}</span></li>`;
      if (typeof hooks?.onSignOut === "function") {
        // Brief delay so the message is readable before we remount login.
        setTimeout(() => hooks.onSignOut(), 1500);
      }
    }
  }

  // -----------------------------------------------------------------
  // Drill: artist → albums.
  // -----------------------------------------------------------------
  async function loadArtist(artistId, btn) {
    state.currentArtistId = artistId;
    state.currentAlbumId = null;
    state.currentTrackId = null;
    updateHash();
    markActive(".pane-sidebar", btn);
    // Leave radio / starred mode if we were in either — both body classes
    // collapse one of the panes via `_app.css`, and an album drill-in
    // needs all three panes visible again.
    document.body.classList.remove("is-radio");
    document.body.classList.remove("is-starred");
    const albumsTitle = document.getElementById("albums-title");
    if (albumsTitle) albumsTitle.textContent = "Albums";
    const pane = document.getElementById("albums-pane");
    pane.innerHTML = `<p class="empty">Loading albums…</p>`;
    document.getElementById("tracks-pane").innerHTML =
      `<p class="empty">Pick an album to see tracks.</p>`;
    try {
      const inner = await client.query("getArtist", { id: artistId });
      const albums = inner?.artist?.album || [];
      const heading = inner?.artist?.name || "Albums";
      document.getElementById("albums-title").textContent = heading;
      if (albums.length === 0) {
        pane.innerHTML = `<p class="empty">No albums for this artist.</p>`;
        return;
      }
      const list = document.createElement("ul");
      list.className = "row-list";
      for (const album of albums) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "row-button album-row";
        button.dataset.albumId = album.id;
        button.innerHTML = `
          <img class="album-thumb" alt="" loading="lazy"
               src="${client.mediaUrl("getCoverArt", { id: album.id, size: 64 })}"
               onerror="this.onerror=null;this.src='data:image/svg+xml;utf8,&lt;svg xmlns=%22http://www.w3.org/2000/svg%22/&gt;'">
          <span class="album-text">
            <span class="album-title">${escapeHtml(album.name || "—")}</span>
            <span class="album-meta">
              ${album.year ? `<span class="album-year">${escapeHtml(String(album.year))}</span>` : ""}
              <span class="album-tracks">${album.songCount || 0} tracks</span>
            </span>
          </span>
        `;
        button.addEventListener("click", () => loadAlbum(album.id, button));
        li.appendChild(button);
        list.appendChild(li);
      }
      pane.innerHTML = "";
      pane.appendChild(list);
    } catch (e) {
      pane.innerHTML = `<p class="empty">Failed: ${escapeHtml(e?.message || String(e))}</p>`;
    }
  }

  // -----------------------------------------------------------------
  // Drill: album → tracks.
  // -----------------------------------------------------------------
  async function loadAlbum(albumId, btn) {
    state.currentAlbumId = albumId;
    state.currentTrackId = null;
    updateHash();
    markActive(".pane-albums", btn);
    const pane = document.getElementById("tracks-pane");
    pane.innerHTML = `<p class="empty">Loading tracks…</p>`;
    try {
      const inner = await client.query("getAlbum", { id: albumId });
      const album = inner?.album || {};
      const songs = album.song || [];
      document.getElementById("tracks-title").textContent = "Tracks";
      if (songs.length === 0) {
        pane.innerHTML = `<p class="empty">No tracks on this album.</p>`;
        return;
      }
      const heading = document.createElement("div");
      heading.className = "album-heading";
      heading.dataset.year = album.year || "";
      heading.innerHTML = `
        <span class="album-heading-title">${escapeHtml(album.name || "—")}</span>
        <span class="album-heading-artist">${escapeHtml(album.artist || "—")}</span>
      `;
      const tableHeader = buildTrackTableHeader();
      const rule = document.createElement("div");
      rule.className = "track-table-rule";
      const list = document.createElement("ul");
      list.className = "row-list track-list";
      for (const song of songs) {
        const li = document.createElement("li");
        const button = buildTrackButton(song, album, song.track ?? "");
        button.addEventListener("click", (event) => {
          if (event.target.closest(".track-star")) {
            toggleStar(button);
            return;
          }
          onTrackClick(button, songs, album);
        });
        li.appendChild(button);
        list.appendChild(li);
      }
      pane.innerHTML = "";
      pane.appendChild(heading);
      pane.appendChild(tableHeader);
      pane.appendChild(rule);
      pane.appendChild(list);
    } catch (e) {
      pane.innerHTML = `<p class="empty">Failed: ${escapeHtml(e?.message || String(e))}</p>`;
    }
  }

  // -----------------------------------------------------------------
  // Track-row builder shared by `loadAlbum` (album drill-in) and
  // `loadStarred` (flat starred-tracks list).
  //
  // `displayNum` is the leading number cell — track number for normal
  // albums, sequential 1..N for the starred list (which spans albums
  // so the original `song.track` would mean nothing).
  // -----------------------------------------------------------------
  function buildTrackTableHeader() {
    const tableHeader = document.createElement("div");
    tableHeader.className = "track-table-header";
    tableHeader.innerHTML = `
      <span class="th-no">#</span>
      <span class="th-title">Title</span>
      <span class="th-artist">Artist</span>
      <span class="th-time">Time</span>
      <span class="th-star"></span>
    `;
    return tableHeader;
  }

  function buildTrackButton(song, album, displayNum) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "row-button track-row";
    button.dataset.trackId = song.id;
    button.dataset.albumId = album.id;
    button.dataset.title = song.title || "—";
    button.dataset.artist = song.artist || album.artist || "—";
    button.dataset.albumTitle = album.name || "";
    button.dataset.albumYear = album.year || "";
    button.dataset.suffix = song.suffix || "";
    const isStarred = Boolean(song.starred);
    if (isStarred) button.dataset.starred = String(song.starred);
    button.innerHTML = `
      <span class="track-no">${displayNum}</span>
      <span class="track-title"><span class="marquee-inner">${escapeHtml(song.title || "—")}</span></span>
      <span class="track-artist">${escapeHtml(song.artist || album.artist || "—")}</span>
      <span class="track-time">${fmtTime(song.duration ?? 0)}</span>
      <span class="track-star ${isStarred ? "is-starred" : ""}" aria-label="${isStarred ? "Unstar" : "Star"}" role="button">${isStarred ? ICONS.heartFilled : ICONS.heart}</span>
    `;
    return button;
  }

  async function toggleStar(rowButton) {
    const id = rowButton.dataset.trackId;
    if (!id) return;
    const starEl = rowButton.querySelector(".track-star");
    const wasStarred = starEl.classList.contains("is-starred");
    // Optimistic flip — instant feedback. Reverted if the network
    // call rejects (rare but possible if the server's stars.toml is
    // read-only or auth expired).
    starEl.classList.toggle("is-starred", !wasStarred);
    starEl.innerHTML = wasStarred ? ICONS.heart : ICONS.heartFilled;
    starEl.setAttribute("aria-label", wasStarred ? "Star" : "Unstar");
    if (wasStarred) {
      delete rowButton.dataset.starred;
    } else {
      rowButton.dataset.starred = new Date().toISOString();
    }
    // If we're in the Starred view and just unstarred, drop the row
    // from the DOM — it no longer belongs in this list. Wait until
    // the network call succeeds before pruning so a failure doesn't
    // leave a phantom gap.
    const inStarredView = document.body.classList.contains("is-starred");
    try {
      await client.query(wasStarred ? "unstar" : "star", { id });
      if (inStarredView && wasStarred) {
        // Update queue state so prev / next still walks the remaining
        // rows correctly; remove the queue entry whose id matches.
        const removedIdx = state.queue.findIndex((q) => q.id === id);
        if (removedIdx >= 0) {
          state.queue.splice(removedIdx, 1);
          if (state.queueIndex > removedIdx) state.queueIndex -= 1;
          else if (state.queueIndex === removedIdx) state.queueIndex = -1;
        }
        rowButton.closest("li")?.remove();
        // Renumber the visible rows so the # column stays 1..N.
        const remaining = document.querySelectorAll(
          "#tracks-pane .track-row .track-no",
        );
        remaining.forEach((el, i) => {
          el.textContent = String(i + 1);
        });
        // Refresh the "N tracks" caption in the album heading.
        const heading = document.querySelector(
          "#tracks-pane .album-heading-artist",
        );
        if (heading) heading.textContent = `${remaining.length} tracks`;
      }
    } catch (e) {
      // Revert on failure.
      starEl.classList.toggle("is-starred", wasStarred);
      starEl.innerHTML = wasStarred ? ICONS.heartFilled : ICONS.heart;
      starEl.setAttribute("aria-label", wasStarred ? "Unstar" : "Star");
      if (wasStarred) {
        rowButton.dataset.starred = "rollback";
      } else {
        delete rowButton.dataset.starred;
      }
      console.warn("toggleStar failed:", e);
    }
  }

  // -----------------------------------------------------------------
  // Starred mode — flat list of every starred track across the library.
  // Behaves like a virtual album for queue purposes: clicking a row
  // plays it and walks forward / backward through the same list.
  // -----------------------------------------------------------------
  document.getElementById("starred-toggle")?.addEventListener("click", () => loadStarred());

  async function loadStarred() {
    const starredBtn = document.getElementById("starred-toggle");
    markActive(".pane-sidebar", starredBtn);
    state.currentArtistId = null;
    state.currentAlbumId = null;
    state.currentTrackId = null;
    updateHash();
    document.body.classList.remove("is-radio");
    // `is-starred` hides the albums pane and lets the tracks pane span
    // the remaining width; CSS toggles this via `body.is-starred`.
    document.body.classList.add("is-starred");

    document.getElementById("tracks-title").textContent = "Tracks";
    const pane = document.getElementById("tracks-pane");
    pane.innerHTML = `<p class="empty">Loading starred tracks…</p>`;
    try {
      const inner = await client.query("getStarred2");
      const songs = inner?.starred2?.song || [];
      if (songs.length === 0) {
        pane.innerHTML =
          `<p class="empty">No starred tracks yet. Star a track from an album to add it here.</p>`;
        return;
      }
      const virtualAlbum = { id: "starred", name: "Starred", artist: "Various", year: "" };
      const heading = document.createElement("div");
      heading.className = "album-heading";
      heading.innerHTML = `
        <span class="album-heading-title">Starred</span>
        <span class="album-heading-artist">${songs.length} tracks</span>
      `;
      const tableHeader = buildTrackTableHeader();
      const rule = document.createElement("div");
      rule.className = "track-table-rule";
      const list = document.createElement("ul");
      list.className = "row-list track-list";
      for (const [i, song] of songs.entries()) {
        const li = document.createElement("li");
        // Use the song's own album metadata for queue display, falling
        // back to "Various" when missing — the Subsonic `song_payload`
        // includes `album` / `albumId` so this is usually present.
        const songAlbum = {
          id: song.albumId || virtualAlbum.id,
          name: song.album || virtualAlbum.name,
          artist: song.artist || virtualAlbum.artist,
          year: song.year || "",
        };
        const button = buildTrackButton(song, songAlbum, i + 1);
        button.addEventListener("click", (event) => {
          if (event.target.closest(".track-star")) {
            toggleStar(button);
            return;
          }
          onTrackClick(button, songs, songAlbum);
        });
        li.appendChild(button);
        list.appendChild(li);
      }
      pane.innerHTML = "";
      pane.appendChild(heading);
      pane.appendChild(tableHeader);
      pane.appendChild(rule);
      pane.appendChild(list);
    } catch (e) {
      pane.innerHTML = `<p class="empty">Failed: ${escapeHtml(e?.message || String(e))}</p>`;
    }
  }

  // -----------------------------------------------------------------
  // Playback.
  // -----------------------------------------------------------------
  function onTrackClick(button, songs, album) {
    state.queue = songs.map((s) => ({
      id: s.id,
      title: s.title || "—",
      artist: s.artist || album.artist || "—",
      albumId: album.id,
      albumTitle: album.name || "",
      albumYear: album.year || "",
      suffix: s.suffix || "",
      rowEl: null,
    }));
    // Bind rowEl after building queue so playQueueIndex can mark the
    // correct DOM element.
    const rowButtons = Array.from(document.querySelectorAll(".track-row"));
    state.queue.forEach((item, i) => {
      item.rowEl = rowButtons[i];
    });
    const idx = state.queue.findIndex((q) => q.id === button.dataset.trackId);
    if (idx >= 0) {
      state.currentTrackId = button.dataset.trackId;
      updateHash();
      playQueueIndex(idx);
    }
  }

  function playQueueIndex(idx) {
    if (idx < 0 || idx >= state.queue.length) return;
    const item = state.queue[idx];
    state.queueIndex = idx;

    audio.src = client.mediaUrl("stream", { id: item.id, format: "raw" });
    audio.play().catch((err) => console.warn("playback failed:", err));

    document.querySelectorAll(".row-button.is-playing").forEach((el) => {
      el.classList.remove("is-playing");
    });
    if (item.rowEl) item.rowEl.classList.add("is-playing");

    document.getElementById("np-title").textContent = item.title;
    document.getElementById("np-artist").textContent = item.artist;
    const albumEl = document.getElementById("np-album");
    const sepEl = document.getElementById("np-album-sep");
    if (albumEl) albumEl.textContent = item.albumTitle || "";
    if (sepEl) sepEl.textContent = item.albumTitle ? " · " : "";
    document.getElementById("np-year").textContent = item.albumYear || "—";
    document.getElementById("np-format").textContent = (item.suffix || "").toUpperCase() || "—";
    const cover = document.getElementById("np-cover");
    if (item.albumId) {
      cover.src = client.mediaUrl("getCoverArt", { id: item.albumId, size: 80 });
    }

    updateMediaSessionMetadata(item);

    playButton.disabled = false;
    prevButton.disabled = idx === 0;
    nextButton.disabled = idx === state.queue.length - 1;
  }

  // -----------------------------------------------------------------
  // Media Session API — wires macOS / Windows / Linux media keys
  // (F7/F8/F9 on a MacBook, dedicated transport keys on third-party
  // keyboards, AirPods double/triple-tap, the macOS Control Center
  // "Now Playing" widget) to the same prev / play-pause / next
  // functions the on-screen transport buttons use.
  //
  // Metadata is also pushed via `MediaMetadata` so the OS widget can
  // show the current title / artist / album cover.
  // -----------------------------------------------------------------
  function updateMediaSessionMetadata(item) {
    if (!("mediaSession" in navigator)) return;
    if (item.kind === "radio") {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: item.title || "Radio",
        artist: item.artist || "Radio",
        album: "",
      });
      return;
    }
    const artwork = item.albumId
      ? [
          {
            src: client.mediaUrl("getCoverArt", { id: item.albumId, size: 512 }),
            sizes: "512x512",
            type: "image/jpeg",
          },
        ]
      : [];
    navigator.mediaSession.metadata = new MediaMetadata({
      title: item.title || "",
      artist: item.artist || "",
      album: item.albumTitle || "",
      artwork,
    });
  }

  if ("mediaSession" in navigator) {
    navigator.mediaSession.setActionHandler("play", () => {
      audio.play().catch((err) => console.warn("media-key play failed:", err));
    });
    navigator.mediaSession.setActionHandler("pause", () => {
      audio.pause();
    });
    navigator.mediaSession.setActionHandler("previoustrack", () => {
      prevTrack();
    });
    navigator.mediaSession.setActionHandler("nexttrack", () => {
      nextTrack();
    });
  }

  function nextTrack() {
    if (state.queueIndex < 0) return;
    if (state.queueIndex + 1 < state.queue.length) {
      playQueueIndex(state.queueIndex + 1);
    }
  }
  function prevTrack() {
    if (state.queueIndex <= 0) return;
    playQueueIndex(state.queueIndex - 1);
  }
  function togglePause() {
    if (state.queueIndex < 0) return;
    if (audio.paused) audio.play();
    else audio.pause();
  }

  // Click delegation for the np-card transport buttons.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "play-pause") togglePause();
    else if (action === "next") nextTrack();
    else if (action === "prev") prevTrack();
  });

  // Audio element lifecycle.
  const stateIconEl = document.getElementById("np-state-icon");
  const playGlyph = playButton.firstElementChild;
  audio.addEventListener("play", () => {
    playGlyph.innerHTML = ICONS.pause;
    stateIconEl.textContent = "▶";
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
  });
  audio.addEventListener("pause", () => {
    playGlyph.innerHTML = ICONS.play;
    stateIconEl.textContent = "‖";
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
  });
  audio.addEventListener("ended", () => {
    playGlyph.innerHTML = ICONS.play;
    stateIconEl.textContent = "■";
    nextTrack();
  });
  audio.addEventListener("timeupdate", () => {
    const posEl = document.getElementById("np-pos");
    const durEl = document.getElementById("np-dur");
    const barEl = document.getElementById("np-bar");
    posEl.textContent = fmtTime(audio.currentTime);
    if (audio.duration && isFinite(audio.duration)) {
      durEl.textContent = fmtTime(audio.duration);
      barEl.value = audio.currentTime / audio.duration;
    }
  });
  audio.addEventListener("loadedmetadata", () => {
    if (audio.duration && isFinite(audio.duration)) {
      document.getElementById("np-dur").textContent = fmtTime(audio.duration);
    }
  });

  // -----------------------------------------------------------------
  // Fullscreen visualizer — engine-agnostic layout sizer.
  //
  // Three prior CSS-only attempts (flex, calc-height, abs-position)
  // all collapsed the canvas to 80–150px in both Chromium (Electron)
  // and WebKit (Tauri). Verified via DevTools: `.now-playing-region`
  // itself wasn't growing past ~300px even with `flex: 1 1 0` set,
  // so the issue is layout-engine-deep, not just canvas-specific.
  //
  // shell.css now uses `position: fixed` on the now-playing-region in
  // is-viz mode, anchored below the topbar to the viewport bottom.
  // We just need to:
  //   1. Measure the actual topbar height and feed it to the CSS
  //      custom property (`--mk-topbar-h`) so the `top:` offset is
  //      accurate (the topbar's height varies a little across
  //      Electron/Tauri/macOS native chrome).
  //   2. Pin the canvas to fill its panel via JS (CSS-only height
  //      keeps getting overridden by flex/min-height/aspect-ratio
  //      rules from _app.css).
  // -----------------------------------------------------------------
  function syncFullscreenLayout() {
    const topbar = document.querySelector(".topbar");
    if (topbar) {
      document.body.style.setProperty("--mk-topbar-h", topbar.offsetHeight + "px");
    }
    const panel = document.querySelector(".viz-panel");
    const canvas = document.getElementById("viz-canvas");
    if (!panel || !canvas) return;
    if (document.body.classList.contains("is-viz")) {
      // Read the panel's rendered height (post position:fixed) and
      // size the canvas to fill it minus the floating panel-title.
      const rect = panel.getBoundingClientRect();
      const titleEl = panel.querySelector(".panel-title");
      const titleHeight = titleEl ? titleEl.offsetHeight : 0;
      const cs = getComputedStyle(panel);
      const padTop = parseFloat(cs.paddingTop) || 0;
      const padBottom = parseFloat(cs.paddingBottom) || 0;
      // Use !important via setProperty so flex/min-height base rules
      // can't undo the explicit pixel height.
      const target = Math.max(120, rect.height - titleHeight - padTop - padBottom);
      canvas.style.setProperty("height", target + "px", "important");
      canvas.style.setProperty("width", "100%", "important");
    } else {
      canvas.style.removeProperty("height");
      canvas.style.removeProperty("width");
    }
  }

  const __vizLayoutObserver = new MutationObserver(syncFullscreenLayout);
  __vizLayoutObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("resize", syncFullscreenLayout);
  // Initial pass + a 100ms-delayed pass so position:fixed has settled.
  syncFullscreenLayout();
  setTimeout(syncFullscreenLayout, 100);

  // -----------------------------------------------------------------
  // Click-to-seek on the progress bar.
  // -----------------------------------------------------------------
  const barEl = document.getElementById("np-bar");
  barEl?.addEventListener("click", (event) => {
    if (!audio.duration || !isFinite(audio.duration)) return;
    const rect = barEl.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * audio.duration;
  });
  // The <progress> element itself doesn't focus, but cursor:pointer
  // signals it's clickable. Set inline so we don't need a CSS edit.
  if (barEl) barEl.style.cursor = "pointer";

  // -----------------------------------------------------------------
  // Volume + mute helpers — used by keybinds AND the in-card slider /
  // mute button. The single source of truth is `audio.volume` /
  // `audio.muted`; everything else (slider position, mute glyph,
  // localStorage) re-syncs from the `volumechange` event below.
  // -----------------------------------------------------------------
  function bumpVolume(delta) {
    audio.volume = Math.max(0, Math.min(1, audio.volume + delta));
  }
  function toggleMute() {
    audio.muted = !audio.muted;
  }

  const volumeSlider = document.getElementById("volume-slider");
  const muteButton = document.getElementById("mute-button");
  const muteGlyph = document.getElementById("mute-glyph");

  volumeSlider.value = String(audio.volume);
  // Slider -> audio: drag end fires `change`, drag-in-progress fires
  // `input`; we wire both to `input` so the volume tracks the drag.
  // Moving the slider above zero implicitly unmutes — same behaviour
  // as macOS / browsers.
  volumeSlider.addEventListener("input", () => {
    const v = parseFloat(volumeSlider.value);
    if (Number.isFinite(v)) {
      audio.volume = v;
      if (v > 0 && audio.muted) audio.muted = false;
    }
  });

  muteButton.addEventListener("click", () => toggleMute());

  // `volumechange` fires for both `volume` and `muted` writes, so a
  // single listener keeps the slider, mute glyph, and localStorage in
  // sync regardless of who flipped the value (slider drag, ArrowUp/
  // ArrowDown keybind, mute button, `m` keybind, media-session pause).
  audio.addEventListener("volumechange", () => {
    volumeSlider.value = String(audio.volume);
    const silent = audio.muted || audio.volume === 0;
    muteGlyph.innerHTML = silent ? ICONS.volumeMute : audio.volume < 0.5 ? ICONS.volumeLow : ICONS.volumeHigh;
    muteButton.setAttribute("aria-label", audio.muted ? "Unmute" : "Mute");
    try {
      localStorage.setItem(LS_VOLUME, String(audio.volume));
    } catch (e) {
      // localStorage unavailable.
    }
  });
  // Fire once so the icon matches the restored volume on boot.
  audio.dispatchEvent(new Event("volumechange"));
  function seekRelative(deltaSec) {
    if (!audio.duration || !isFinite(audio.duration)) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + deltaSec));
  }

  // -----------------------------------------------------------------
  // Shortcut help modal — `?` toggles.
  // -----------------------------------------------------------------
  function toggleShortcutHelp() {
    const existing = document.getElementById("shortcut-help");
    if (existing) {
      existing.remove();
      return;
    }
    const overlay = document.createElement("div");
    overlay.id = "shortcut-help";
    overlay.className = "shortcut-help-overlay";
    overlay.innerHTML = `
      <div class="shortcut-help-card">
        <h2>Keyboard shortcuts</h2>
        <table class="shortcut-help-table">
          <tr><td><kbd>Space</kbd></td><td>Play / pause</td></tr>
          <tr><td><kbd>n</kbd></td><td>Next track</td></tr>
          <tr><td><kbd>p</kbd></td><td>Previous track</td></tr>
          <tr><td><kbd>←</kbd> / <kbd>→</kbd></td><td>Seek -5s / +5s</td></tr>
          <tr><td><kbd>↑</kbd> / <kbd>↓</kbd></td><td>Volume up / down</td></tr>
          <tr><td><kbd>m</kbd></td><td>Mute / unmute</td></tr>
          <tr><td><kbd>f</kbd></td><td>Fullscreen visualizer</td></tr>
          <tr><td><kbd>/</kbd></td><td>Focus filter</td></tr>
          <tr><td><kbd>Esc</kbd></td><td>Close modal / exit fullscreen</td></tr>
          <tr><td><kbd>?</kbd></td><td>Toggle this panel</td></tr>
        </table>
        <p class="shortcut-help-hint">Click anywhere or press Esc to close.</p>
      </div>
    `;
    overlay.addEventListener("click", () => overlay.remove());
    document.body.appendChild(overlay);
  }

  // Keybinds.
  document.addEventListener("keydown", (event) => {
    const tag = (event.target && event.target.tagName) || "";
    const inField = tag === "INPUT" || tag === "TEXTAREA";
    if (event.key === "/" && event.target !== document.getElementById("search")) {
      event.preventDefault();
      document.getElementById("search")?.focus();
      return;
    }
    if (inField) return;
    if (event.key === "Escape") {
      const help = document.getElementById("shortcut-help");
      if (help) {
        help.remove();
        return;
      }
      // Visualizer fullscreen handles its own Escape.
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePause();
    } else if (event.key === "n" || event.key === "N") {
      nextTrack();
    } else if (event.key === "p" || event.key === "P") {
      prevTrack();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekRelative(5);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekRelative(-5);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      bumpVolume(0.05);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      bumpVolume(-0.05);
    } else if (event.key === "m" || event.key === "M") {
      toggleMute();
    } else if (event.key === "?") {
      event.preventDefault();
      toggleShortcutHelp();
    }
  });

  // -----------------------------------------------------------------
  // Search dropdown — typeahead under #search.
  //
  // Calls `/rest/search3` on a 250ms debounce and shows top hits for
  // Artists / Albums / Tracks in a popover below the input. Clicking
  // a hit routes through the existing drill-in / playback paths so
  // post-click state matches what you'd get from manual navigation.
  // Stale-response guard via a monotonic sequence counter — a slow
  // request that lands after a newer one is discarded.
  // -----------------------------------------------------------------
  const searchInput = document.getElementById("search");
  const searchDropdown = document.getElementById("search-dropdown");
  const searchEmptyMsg = searchDropdown.querySelector(".search-empty");
  const sectionByKind = {
    artist: searchDropdown.querySelector('.search-section[data-kind="artist"]'),
    album: searchDropdown.querySelector('.search-section[data-kind="album"]'),
    song: searchDropdown.querySelector('.search-section[data-kind="song"]'),
  };
  let searchDebounce = null;
  let searchSeq = 0;
  let searchActiveIdx = -1;

  function searchItemButtons() {
    return Array.from(searchDropdown.querySelectorAll(".search-item-button"));
  }

  function setSearchActive(idx) {
    const buttons = searchItemButtons();
    if (buttons.length === 0) {
      searchActiveIdx = -1;
      return;
    }
    if (idx < 0) idx = buttons.length - 1;
    if (idx >= buttons.length) idx = 0;
    buttons.forEach((b, i) => b.classList.toggle("is-active", i === idx));
    buttons[idx].scrollIntoView({ block: "nearest" });
    searchActiveIdx = idx;
  }

  function closeSearchDropdown() {
    searchDropdown.hidden = true;
    searchActiveIdx = -1;
  }

  function clearSearch() {
    searchInput.value = "";
    closeSearchDropdown();
  }

  function buildSearchItem(kind, item) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "search-item-button";
    if (kind === "artist") {
      btn.innerHTML = `<span class="search-item-primary">${escapeHtml(item.name || "—")}</span>`;
      btn.addEventListener("click", () => {
        clearSearch();
        // Route through the existing artist button so highlighting and
        // active-pane state match what manual navigation would produce.
        const target = document.querySelector(
          `.pane-sidebar .row-button[data-artist-id="${cssEscape(item.id)}"]`,
        );
        if (target) {
          target.scrollIntoView({ block: "nearest" });
          target.click();
        } else {
          loadArtist(item.id, null);
        }
      });
    } else if (kind === "album") {
      const meta = [item.artist, item.year ? String(item.year) : null].filter(Boolean).join(" · ");
      btn.innerHTML = `
        <span class="search-item-primary">${escapeHtml(item.name || "—")}</span>
        <span class="search-item-secondary">${escapeHtml(meta || "—")}</span>
      `;
      btn.addEventListener("click", async () => {
        clearSearch();
        // Drill via the artist first so the sidebar highlight + albums
        // pane content reflect where the album lives. Then click the
        // album button once it renders so the album-row gets `is-active`.
        if (item.artistId) {
          const artistBtn = document.querySelector(
            `.pane-sidebar .row-button[data-artist-id="${cssEscape(item.artistId)}"]`,
          );
          if (artistBtn) {
            artistBtn.scrollIntoView({ block: "nearest" });
            artistBtn.click();
          } else {
            await loadArtist(item.artistId, null);
          }
        }
        const albumBtn = await waitForElement(
          `.pane-albums .row-button[data-album-id="${cssEscape(item.id)}"]`,
        );
        if (albumBtn) {
          albumBtn.scrollIntoView({ block: "nearest" });
          albumBtn.click();
        } else {
          loadAlbum(item.id, null);
        }
      });
    } else {
      // song
      const meta = [item.artist, item.album].filter(Boolean).join(" · ");
      btn.innerHTML = `
        <span class="search-item-primary">${escapeHtml(item.title || "—")}</span>
        <span class="search-item-secondary">${escapeHtml(meta || "—")}</span>
      `;
      btn.addEventListener("click", async () => {
        clearSearch();
        // Drill artist -> album so the surrounding context (sidebar
        // highlight + albums pane + full track list) is visible, then
        // click the track row so onTrackClick builds the proper queue
        // (whole album, with `nextTrack` etc) and marks the row as
        // `is-playing`.
        if (item.artistId) {
          const artistBtn = document.querySelector(
            `.pane-sidebar .row-button[data-artist-id="${cssEscape(item.artistId)}"]`,
          );
          if (artistBtn) {
            artistBtn.scrollIntoView({ block: "nearest" });
            artistBtn.click();
          }
        }
        if (item.albumId) {
          const albumBtn = await waitForElement(
            `.pane-albums .row-button[data-album-id="${cssEscape(item.albumId)}"]`,
          );
          if (albumBtn) {
            albumBtn.scrollIntoView({ block: "nearest" });
            albumBtn.click();
          }
        }
        const trackBtn = await waitForElement(
          `.pane-tracks .row-button[data-track-id="${cssEscape(item.id)}"]`,
        );
        if (trackBtn) {
          trackBtn.scrollIntoView({ block: "center" });
          trackBtn.click();
        } else {
          // Fallback when we can't drill (search hit a track whose
          // artist or album isn't in the indexed list): play as a
          // one-item queue so the Now Playing card still updates.
          state.queue = [
            {
              id: item.id,
              title: item.title || "—",
              artist: item.artist || "—",
              albumId: item.albumId || null,
              albumTitle: item.album || "",
              albumYear: item.year ? String(item.year) : "",
              suffix: item.suffix || "",
              rowEl: null,
            },
          ];
          state.currentTrackId = item.id;
          updateHash();
          playQueueIndex(0);
        }
      });
    }
    li.appendChild(btn);
    return li;
  }

  function renderSearchResults(result) {
    const artists = result?.artist || [];
    const albums = result?.album || [];
    const songs = result?.song || [];
    const buckets = { artist: artists, album: albums, song: songs };
    let anyVisible = false;
    for (const kind of ["artist", "album", "song"]) {
      const section = sectionByKind[kind];
      const list = section.querySelector(".search-section-list");
      list.innerHTML = "";
      const items = buckets[kind];
      if (items.length === 0) {
        section.hidden = true;
        continue;
      }
      section.hidden = false;
      anyVisible = true;
      for (const item of items) {
        list.appendChild(buildSearchItem(kind, item));
      }
    }
    searchEmptyMsg.hidden = anyVisible;
    searchDropdown.hidden = false;
    // Reset highlight to "none" — Down arrow will land on the first
    // result; results re-rendering should not silently keep an old
    // pointer that might now reference a different row.
    searchActiveIdx = -1;
    searchItemButtons().forEach((b) => b.classList.remove("is-active"));
  }

  async function runSearch(query) {
    const mySeq = ++searchSeq;
    try {
      const inner = await client.query("search3", {
        query,
        artistCount: 6,
        albumCount: 8,
        songCount: 12,
      });
      if (mySeq !== searchSeq) return;
      renderSearchResults(inner?.searchResult3 || {});
    } catch (e) {
      if (mySeq !== searchSeq) return;
      console.warn("search3 failed:", e);
    }
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    const q = searchInput.value.trim();
    if (!q) {
      closeSearchDropdown();
      return;
    }
    searchDebounce = setTimeout(() => runSearch(q), 250);
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      clearSearch();
      searchInput.blur();
      return;
    }
    if (searchDropdown.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSearchActive(searchActiveIdx + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSearchActive(searchActiveIdx - 1);
    } else if (event.key === "Enter") {
      const buttons = searchItemButtons();
      if (searchActiveIdx >= 0 && searchActiveIdx < buttons.length) {
        event.preventDefault();
        buttons[searchActiveIdx].click();
      }
    }
  });

  // Click outside the dropdown closes it (the input itself is part of
  // its own container so typing keeps it open).
  document.addEventListener("click", (event) => {
    if (
      !searchDropdown.contains(event.target) &&
      event.target !== searchInput &&
      !searchDropdown.hidden
    ) {
      closeSearchDropdown();
    }
  });

  // -----------------------------------------------------------------
  // Cross-client refresh — when the window regains focus or becomes
  // visible again, re-fetch the currently-shown view so star changes
  // made from another client (the web UI, an iPhone Subsonic client)
  // appear without a manual Cmd+R. Refresh-on-focus is zero-cost when
  // idle (no polling) and instantly current when the user comes back
  // to the window — the right tradeoff for syncs that happen rarely
  // but matter when they do (star a track on the phone during a
  // commute, open the desktop at home, see it already ♥).
  // -----------------------------------------------------------------
  async function refreshCurrentView() {
    if (document.body.classList.contains("is-starred")) {
      await loadStarred();
      return;
    }
    if (state.currentAlbumId) {
      // Re-fetch the album; this regenerates the track rows with
      // fresh `starred` state. We avoid touching the queue so the
      // currently-playing track isn't disturbed.
      await loadAlbum(state.currentAlbumId, null);
    }
  }
  // Both events fire on the same return-to-window action depending on
  // OS / engine; together they cover Electron, Tauri, and any plain
  // browser tab. The handler is idempotent so a double fire just
  // costs one extra fetch.
  window.addEventListener("focus", () => {
    refreshCurrentView().catch((e) => console.warn("focus-refresh failed:", e));
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshCurrentView().catch((e) => console.warn("visibility-refresh failed:", e));
    }
  });
}

// ---------------------------------------------------------------------------
// Helpers (kept local — render-time stuff specific to this module).
// ---------------------------------------------------------------------------

function fmtTime(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "00:00";
  const s = Math.floor(seconds);
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

function markActive(paneSelector, button) {
  document.querySelectorAll(`${paneSelector} .row-button.is-active`).forEach((el) => {
    el.classList.remove("is-active");
  });
  button?.classList.add("is-active");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
