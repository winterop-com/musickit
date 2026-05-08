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

/** Mount the shell into `root`. Takes the SubsonicClient + session. */
export function renderShell(root, client, session, hooks = {}) {
  root.innerHTML = `
    <header class="topbar">
      <div class="topbar-left">
        <input type="search" id="search" class="search-input"
               placeholder="filter / search…  (/)" autocomplete="off" spellcheck="false">
      </div>
      <div class="topbar-center">
        <span class="brand">MusicKit</span>
        <span class="version" id="server-info"></span>
      </div>
      <div class="topbar-right">
        <span class="user" id="topbar-user"></span>
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
              <span aria-hidden="true">⏮</span>
            </button>
            <button type="button" class="play-button" data-action="play-pause"
                    id="play-button" disabled aria-label="Play / pause">
              <span aria-hidden="true">▶</span>
            </button>
            <button type="button" class="np-button" data-action="next"
                    id="next-button" disabled aria-label="Next track">
              <span aria-hidden="true">⏭</span>
            </button>
          </div>
        </div>
        <div class="np-progress" id="np-progress">
          <span class="np-state-icon" id="np-state-icon">■</span>
          <span class="np-pos" id="np-pos">00:00</span>
          <progress id="np-bar" max="1" value="0"></progress>
          <span class="np-dur" id="np-dur">00:00</span>
        </div>
      </section>
    </section>

    <main class="three-pane">
      <section class="pane pane-sidebar" aria-label="Artists">
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
      const list = document.getElementById("artists-list");
      list.innerHTML = `<li class="empty">Failed to load artists: ${escapeHtml(e?.message || String(e))}</li>`;
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
      const tableHeader = document.createElement("div");
      tableHeader.className = "track-table-header";
      tableHeader.innerHTML = `
        <span class="th-no">#</span>
        <span class="th-title">Title</span>
        <span class="th-artist">Artist</span>
        <span class="th-time">Time</span>
      `;
      const rule = document.createElement("div");
      rule.className = "track-table-rule";
      const list = document.createElement("ul");
      list.className = "row-list track-list";
      for (const song of songs) {
        const li = document.createElement("li");
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
        button.innerHTML = `
          <span class="track-no">${song.track ?? ""}</span>
          <span class="track-title"><span class="marquee-inner">${escapeHtml(song.title || "—")}</span></span>
          <span class="track-artist">${escapeHtml(song.artist || album.artist || "—")}</span>
          <span class="track-time">${fmtTime(song.duration ?? 0)}</span>
        `;
        button.addEventListener("click", () => onTrackClick(button, songs, album));
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

    playButton.disabled = false;
    prevButton.disabled = idx === 0;
    nextButton.disabled = idx === state.queue.length - 1;
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
    playGlyph.textContent = "‖";
    stateIconEl.textContent = "▶";
  });
  audio.addEventListener("pause", () => {
    playGlyph.textContent = "▶";
    stateIconEl.textContent = "‖";
  });
  audio.addEventListener("ended", () => {
    playGlyph.textContent = "▶";
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

  // Keybinds — a minimal set for now; full palette / repeat / shuffle
  // ride along in Phase D.
  document.addEventListener("keydown", (event) => {
    const tag = (event.target && event.target.tagName) || "";
    const inField = tag === "INPUT" || tag === "TEXTAREA";
    if (event.key === "/" && event.target !== document.getElementById("search")) {
      event.preventDefault();
      document.getElementById("search")?.focus();
      return;
    }
    if (inField) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePause();
    } else if (event.key === "n" || event.key === "N") {
      nextTrack();
    } else if (event.key === "p" || event.key === "P") {
      prevTrack();
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
