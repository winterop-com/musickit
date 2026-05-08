// musickit web UI — vanilla JS, no framework.
//
// State lives in `state` plus on the DOM (data-* attributes on rows).
// Queue model: clicking a track in the tracks pane builds a queue from
// all visible tracks in that pane (in DOM order); audio.ended advances;
// n/p keybinds + buttons step through.
//
// Search: typing into #search debounces a fetch to /web/search?q=, swaps
// results into the right pane. Empty query → no-op (panes restore on the
// next normal click).
//
// Lyrics: `l` toggles a fixed-position overlay panel on the right. When
// the playing track has synced LRC, `audio.timeupdate` highlights the
// active line.

(function () {
  "use strict";

  const audio = document.getElementById("audio");
  const playButton = document.getElementById("play-button");
  const prevButton = document.getElementById("prev-button");
  const nextButton = document.getElementById("next-button");
  const npTitle = document.getElementById("np-title");
  const npArtist = document.getElementById("np-artist");
  const npAlbum = document.getElementById("np-album");
  const npAlbumSep = document.getElementById("np-album-sep");
  const npYear = document.getElementById("np-year");
  const npFormat = document.getElementById("np-format");
  const npCover = document.getElementById("np-cover");
  const npPos = document.getElementById("np-pos");
  const npDur = document.getElementById("np-dur");
  const npBar = document.getElementById("np-bar");
  const npStateIcon = document.getElementById("np-state-icon");
  const npVol = document.getElementById("np-vol");
  // StatusBar
  const sbVol = document.getElementById("sb-vol");
  const sbVolBar = document.getElementById("sb-vol-bar");
  const sbRepeat = document.getElementById("sb-repeat");
  const sbShuffle = document.getElementById("sb-shuffle");
  const sbAlbum = document.getElementById("sb-album");
  const sbCursor = document.getElementById("sb-cursor");
  const sbTime = document.getElementById("sb-time");
  const searchInput = document.getElementById("search");
  const lyricsPanel = document.getElementById("lyrics-panel");
  const lyricsBody = document.getElementById("lyrics-body");
  const lyricsClose = document.getElementById("lyrics-close");
  const albumsPane = document.getElementById("albums-pane");
  const tracksPane = document.getElementById("tracks-pane");
  const albumsPaneTitle = document.querySelector(".pane-albums .panel-title");
  const tracksPaneTitle = document.querySelector(".pane-tracks .panel-title");

  const state = {
    queue: [], // [{ id, title, artist, albumId, rowEl }]
    queueIndex: -1, // -1 = nothing playing
    lyricsLines: [], // [{start_ms, text}] when synced; empty otherwise
    lyricsSynced: false,
    lyricsTrackId: null,
    // Playback modes — match the TUI's order. `r` cycles off → album →
    // track → off; `s` toggles shuffle.
    repeat: "off", // "off" | "album" | "track"
    shuffle: false,
    // Radio metadata polling (ICY StreamTitle from the proxy).
    radioMetaTimer: null,
    radioStationName: "", // remembered separately so we can fall back when
    // the upstream stops emitting StreamTitle frames.
  };

  // -------------------------------------------------------------------- //
  // Helpers                                                              //
  // -------------------------------------------------------------------- //

  function fmtTime(seconds) {
    if (!isFinite(seconds) || seconds <= 0) return "00:00";
    const s = Math.floor(seconds);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  async function fetchFragment(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      return `<p class="empty">Failed to load: ${response.status} ${response.statusText}</p>`;
    }
    return await response.text();
  }

  async function loadInto(url, targetEl) {
    targetEl.innerHTML = await fetchFragment(url);
  }

  function markActiveRow(button, paneSelector) {
    document.querySelectorAll(paneSelector + " .row-button.is-active").forEach((el) => {
      el.classList.remove("is-active");
    });
    button.classList.add("is-active");
  }

  // -------------------------------------------------------------------- //
  // Queue                                                                //
  // -------------------------------------------------------------------- //

  function buildQueueFromVisibleTracks() {
    const rows = Array.from(tracksPane.querySelectorAll(".track-row"));
    // The album heading lives in the same fragment — pull title/artist
    // off it once so each track inherits the right album metadata in
    // the now-playing card.
    const headingEl = tracksPane.querySelector(".album-heading");
    const albumTitle = headingEl?.querySelector(".album-heading-title")?.textContent || "";
    const albumYear = headingEl?.dataset.year || "";
    return rows.map((rowEl) => ({
      id: rowEl.dataset.id,
      title: rowEl.dataset.title,
      artist: rowEl.dataset.artist,
      albumId: rowEl.dataset.albumId,
      albumTitle,
      albumYear,
      rowEl,
    }));
  }

  function playQueueIndex(idx) {
    if (idx < 0 || idx >= state.queue.length) return;
    const item = state.queue[idx];
    state.queueIndex = idx;

    // Radio items go through the same-origin /web/radio-stream proxy so
    // the visualizer's `crossOrigin = "anonymous"` doesn't trigger a CORS
    // preflight against Icecast/SHOUTcast servers (which mostly don't
    // return CORS headers — playback would fail silently). Tracks ride
    // the regular Subsonic /rest/stream endpoint.
    if (item.kind === "radio") {
      audio.src = "/web/radio-stream?url=" + encodeURIComponent(item.url);
      state.radioStationName = item.title || "";
      startRadioMetaPoll(item.url);
    } else {
      audio.src = "/rest/stream?id=" + encodeURIComponent(item.id) + "&f=raw";
      stopRadioMetaPoll();
      state.radioStationName = "";
    }
    audio.play().catch((err) => console.warn("playback failed:", err));

    // Visual: orange-out the playing row. Broadened from `.track-row` to
    // `.row-button` so both album-track rows AND radio-station rows get
    // cleared when something new starts playing.
    document.querySelectorAll(".row-button.is-playing").forEach((el) => {
      el.classList.remove("is-playing");
    });
    if (item.rowEl) {
      item.rowEl.classList.add("is-playing");
    }

    npTitle.textContent = item.title || "—";
    // Radio: the station name belongs in the artist line so the
    // top-right title row can flip to the StreamTitle (current song)
    // once /web/radio-meta polls return one.
    if (item.kind === "radio") {
      npArtist.textContent = item.title || "Radio";
    } else {
      npArtist.textContent = item.artist || "—";
    }
    if (npAlbum) {
      npAlbum.textContent = item.albumTitle || "";
      if (npAlbumSep) npAlbumSep.textContent = item.albumTitle ? " · " : "";
    }
    if (npYear) npYear.textContent = item.albumYear || "—";
    if (npFormat) npFormat.textContent = item.kind === "radio" ? "Stream" : "AAC";
    if (sbAlbum) sbAlbum.textContent = item.albumTitle || "—";
    if (sbCursor) {
      const idx = state.queue.findIndex((q) => q.id === item.id);
      sbCursor.textContent = `${idx + 1}/${state.queue.length}`;
    }
    if (item.kind === "radio") {
      // No cover for stations — keep the slot collapsed.
      npCover.style.visibility = "hidden";
    } else if (item.albumId) {
      npCover.src = "/rest/getCoverArt?id=" + encodeURIComponent(item.albumId) + "&size=80";
      npCover.style.visibility = "visible";
    } else {
      npCover.style.visibility = "hidden";
    }
    playButton.disabled = false;
    prevButton.disabled = idx === 0;
    nextButton.disabled = idx === state.queue.length - 1;

    // Auto-load lyrics if the panel is open. Radio has no lyrics.
    if (item.kind === "radio") {
      lyricsBody.innerHTML = '<p class="empty">Radio streams have no lyrics.</p>';
      state.lyricsLines = [];
      state.lyricsSynced = false;
      state.lyricsTrackId = null;
    } else if (lyricsPanel.classList.contains("is-open")) {
      loadLyricsFor(item.id);
    } else {
      state.lyricsTrackId = null; // invalidate so a future toggle reloads
    }
  }

  function nextTrack() {
    if (state.queueIndex < 0) return;
    if (state.shuffle && state.queue.length > 1) {
      // Pick a random index different from the current one. With
      // `repeat == "off"` we still allow re-picking after the queue
      // exhausts; tracking played-indices isn't worth the complexity
      // for a 10-30 track album queue.
      let idx = state.queueIndex;
      while (idx === state.queueIndex) {
        idx = Math.floor(Math.random() * state.queue.length);
      }
      playQueueIndex(idx);
      return;
    }
    if (state.queueIndex + 1 < state.queue.length) {
      playQueueIndex(state.queueIndex + 1);
    }
  }

  function prevTrack() {
    if (state.queueIndex <= 0) return;
    playQueueIndex(state.queueIndex - 1);
  }

  function cycleRepeat() {
    const order = ["off", "album", "track"];
    state.repeat = order[(order.indexOf(state.repeat) + 1) % order.length];
    updateRepeatShuffleReadout();
  }

  function toggleShuffle() {
    state.shuffle = !state.shuffle;
    updateRepeatShuffleReadout();
  }

  function updateRepeatShuffleReadout() {
    if (sbRepeat) sbRepeat.textContent = state.repeat;
    if (sbShuffle) sbShuffle.textContent = state.shuffle ? "on" : "off";
  }

  function togglePause() {
    if (state.queueIndex < 0) return;
    if (audio.paused) audio.play();
    else audio.pause();
  }

  function adjustVolume(delta) {
    const v = Math.max(0, Math.min(1, audio.volume + delta));
    audio.volume = v;
    npVol.value = v;
    updateVolumeReadout(v);
  }

  function seekBy(seconds) {
    if (!audio.duration || !isFinite(audio.duration)) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds));
  }

  // -------------------------------------------------------------------- //
  // Radio ICY metadata polling                                           //
  //                                                                      //
  // Browsers strip ICY frames from <audio> before they reach JS. Our     //
  // /web/radio-stream proxy parses them server-side and stashes the      //
  // last-seen StreamTitle; this poller just reads that cache.            //
  // -------------------------------------------------------------------- //

  function stopRadioMetaPoll() {
    if (state.radioMetaTimer) {
      clearInterval(state.radioMetaTimer);
      state.radioMetaTimer = null;
    }
  }

  function startRadioMetaPoll(stationUrl) {
    stopRadioMetaPoll();
    const poll = async () => {
      try {
        const res = await fetch("/web/radio-meta?url=" + encodeURIComponent(stationUrl), {
          credentials: "same-origin",
        });
        if (!res.ok) return;
        const data = await res.json();
        const t = (data.title || "").trim();
        if (!t) return;
        // Render: title row gets the current song; artist row stays as
        // the station name so the listener always sees what they tuned
        // into. StatusBar Album mirrors the song title.
        npTitle.textContent = t;
        if (sbAlbum) sbAlbum.textContent = t;
      } catch (e) {
        // Network blip — try again on the next tick.
      }
    };
    poll(); // immediate
    state.radioMetaTimer = setInterval(poll, 8000);
  }

  // -------------------------------------------------------------------- //
  // Lyrics                                                               //
  // -------------------------------------------------------------------- //

  function toggleLyrics() {
    const wasOpen = lyricsPanel.classList.contains("is-open");
    if (wasOpen) {
      lyricsPanel.classList.remove("is-open");
      return;
    }
    lyricsPanel.classList.add("is-open");
    if (state.queueIndex >= 0) {
      loadLyricsFor(state.queue[state.queueIndex].id);
    } else {
      lyricsBody.innerHTML = '<p class="empty">No track playing.</p>';
    }
  }

  async function loadLyricsFor(trackId) {
    if (state.lyricsTrackId === trackId) return; // already loaded for this track
    state.lyricsTrackId = trackId;
    lyricsBody.innerHTML = '<p class="empty">Loading…</p>';
    try {
      const response = await fetch(
        "/rest/getLyricsBySongId?id=" + encodeURIComponent(trackId) + "&f=json",
        { credentials: "same-origin" },
      );
      if (!response.ok) throw new Error("HTTP " + response.status);
      const body = await response.json();
      const structured = (body["subsonic-response"] || {}).lyricsList?.structuredLyrics || [];
      if (!structured.length) {
        lyricsBody.innerHTML = '<p class="empty">No lyrics for this track.</p>';
        state.lyricsLines = [];
        state.lyricsSynced = false;
        return;
      }
      const entry = structured[0];
      state.lyricsSynced = !!entry.synced;
      state.lyricsLines = (entry.line || []).map((l) => ({
        start_ms: l.start || 0,
        text: l.value || "",
      }));
      renderLyrics(0);
    } catch (err) {
      console.warn("lyrics fetch failed:", err);
      lyricsBody.innerHTML = '<p class="empty">Failed to load lyrics.</p>';
      state.lyricsLines = [];
      state.lyricsSynced = false;
    }
  }

  function renderLyrics(positionMs) {
    if (!state.lyricsLines.length) {
      lyricsBody.innerHTML = '<p class="empty">No lyrics for this track.</p>';
      return;
    }
    let activeIdx = -1;
    if (state.lyricsSynced) {
      for (let i = 0; i < state.lyricsLines.length; i++) {
        if (state.lyricsLines[i].start_ms <= positionMs) activeIdx = i;
        else break;
      }
    }
    const html = state.lyricsLines
      .map((line, i) => {
        const cls =
          i === activeIdx ? "lyric-line is-active" : i < activeIdx ? "lyric-line is-played" : "lyric-line";
        const text = line.text ? escapeHtml(line.text) : "&nbsp;";
        return `<p class="${cls}">${text}</p>`;
      })
      .join("");
    lyricsBody.innerHTML = html;

    // Auto-scroll the active line into view.
    if (activeIdx >= 0) {
      const activeEl = lyricsBody.querySelectorAll(".lyric-line")[activeIdx];
      if (activeEl && typeof activeEl.scrollIntoView === "function") {
        activeEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // -------------------------------------------------------------------- //
  // Search                                                               //
  // -------------------------------------------------------------------- //

  let searchTimer = null;

  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) {
      // Empty query: leave panes as-is. User can click an artist again.
      return;
    }
    searchTimer = setTimeout(async () => {
      const url = "/web/search?q=" + encodeURIComponent(q);
      const html = await fetchFragment(url);
      // Search results swap into the tracks pane (the widest).
      tracksPane.innerHTML = html;
    }, 200);
  }

  // -------------------------------------------------------------------- //
  // Click delegation                                                     //
  // -------------------------------------------------------------------- //

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "load-artist") {
      markActiveRow(button, ".pane-artists");
      loadInto("/web/artist/" + encodeURIComponent(button.dataset.id), albumsPane);
      tracksPane.innerHTML = '<p class="empty">Pick an album to see tracks.</p>';
      // Leave radio mode: restore tracks pane + the original titles.
      document.body.classList.remove("is-radio");
      if (albumsPaneTitle) albumsPaneTitle.textContent = "Albums";
      if (tracksPaneTitle) tracksPaneTitle.textContent = "Tracks";
    } else if (action === "load-radio") {
      // Radio list goes into the (renamed) middle pane; tracks pane is
      // hidden in radio mode (`body.is-radio` collapses the grid to two
      // columns). The Now Playing card up top is the only "currently
      // playing" surface needed for a stream.
      document.body.classList.add("is-radio");
      if (albumsPaneTitle) albumsPaneTitle.textContent = "Stations";
      loadInto("/web/radio", albumsPane).then(() => {
        // The fragment swap blew away any prior is-playing mark; restore
        // it if a station is currently active. Look up by stream URL —
        // the data-url attribute is the stable identity for stations.
        const cur = state.queueIndex >= 0 ? state.queue[state.queueIndex] : null;
        if (cur && cur.kind === "radio" && cur.url) {
          const sel =
            '[data-action="play-radio"][data-url="' + cur.url.replace(/"/g, '\\"') + '"]';
          const btn = albumsPane.querySelector(sel);
          if (btn) {
            btn.classList.add("is-playing");
            cur.rowEl = btn; // re-bind so subsequent plays clear the right element
          }
        }
      });
    } else if (action === "play-radio") {
      // Single-item queue. The clicked button itself is the rowEl so
      // playQueueIndex can mark it `is-playing` (same pattern as tracks).
      const station = {
        kind: "radio",
        id: "radio:" + (button.dataset.url || ""),
        title: button.dataset.name || "Radio",
        artist: "Radio",
        url: button.dataset.url,
        albumId: "",
        albumTitle: "",
        albumYear: "",
        rowEl: button,
      };
      state.queue = [station];
      playQueueIndex(0);
    } else if (action === "load-album") {
      markActiveRow(button, ".pane-albums");
      loadInto("/web/album/" + encodeURIComponent(button.dataset.id), tracksPane);
    } else if (action === "play-track") {
      // Defer queue construction until after the click bubbles, so the
      // .is-playing class set inside playQueueIndex applies cleanly.
      state.queue = buildQueueFromVisibleTracks();
      const idx = state.queue.findIndex((q) => q.id === button.dataset.id);
      playQueueIndex(idx);
    } else if (action === "play-pause") {
      togglePause();
    } else if (action === "next") {
      nextTrack();
    } else if (action === "prev") {
      prevTrack();
    } else if (action === "toggle-lyrics") {
      toggleLyrics();
    } else if (action === "cycle-repeat") {
      cycleRepeat();
    } else if (action === "toggle-shuffle") {
      toggleShuffle();
    }
  });

  if (lyricsClose) {
    lyricsClose.addEventListener("click", function () {
      lyricsPanel.classList.remove("is-open");
    });
  }

  // -------------------------------------------------------------------- //
  // Audio element wiring                                                 //
  // -------------------------------------------------------------------- //

  function setStateIcon(icon) {
    if (npStateIcon) npStateIcon.textContent = icon;
  }

  audio.addEventListener("play", function () {
    playButton.classList.remove("is-paused");
    playButton.firstElementChild.textContent = "‖";
    setStateIcon("▶");
  });

  audio.addEventListener("pause", function () {
    playButton.classList.add("is-paused");
    playButton.firstElementChild.textContent = "▶";
    setStateIcon("‖");
  });

  audio.addEventListener("ended", function () {
    playButton.classList.remove("is-paused");
    playButton.firstElementChild.textContent = "▶";
    setStateIcon("■");
    // Repeat behaviour mirrors the TUI:
    //   off   — advance to next, stop at end
    //   album — advance, wrap to index 0 at the end
    //   track — replay current track
    if (state.repeat === "track" && state.queueIndex >= 0) {
      playQueueIndex(state.queueIndex);
      return;
    }
    if (state.shuffle && state.queue.length > 1) {
      nextTrack();
      return;
    }
    if (state.queueIndex + 1 >= state.queue.length) {
      if (state.repeat === "album" && state.queue.length > 0) {
        playQueueIndex(0);
      }
      return;
    }
    nextTrack();
  });

  audio.addEventListener("timeupdate", function () {
    const current = state.queueIndex >= 0 ? state.queue[state.queueIndex] : null;
    const isRadio = current && current.kind === "radio";
    if (isRadio) {
      // Streams have no finite duration. Show only the elapsed listen
      // time; clear the progress bar and the StatusBar duration cell.
      const pos = fmtTime(audio.currentTime);
      npPos.textContent = pos;
      npDur.textContent = "—";
      npBar.removeAttribute("value");
      if (sbTime) sbTime.textContent = `${pos} / —`;
      return;
    }
    const pos = fmtTime(audio.currentTime);
    npPos.textContent = pos;
    if (audio.duration && isFinite(audio.duration)) {
      const dur = fmtTime(audio.duration);
      npDur.textContent = dur;
      npBar.value = audio.currentTime / audio.duration;
      if (sbTime) sbTime.textContent = `${pos} / ${dur}`;
    }
    if (lyricsPanel.classList.contains("is-open") && state.lyricsSynced) {
      renderLyrics(Math.floor(audio.currentTime * 1000));
    }
  });

  audio.addEventListener("loadedmetadata", function () {
    npDur.textContent = fmtTime(audio.duration);
  });

  npBar.addEventListener("click", function (event) {
    if (!audio.duration) return;
    const rect = npBar.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * audio.duration;
  });

  function updateVolumeReadout(v) {
    if (sbVol) sbVol.textContent = `${Math.round(v * 100)}%`;
    if (sbVolBar) {
      const filled = Math.round(v * 12);
      // Wrap each segment in its own span so the bracket / filled /
      // unfilled portions can be coloured independently. Matches the
      // TUI's `[<green>|||...</green><dim>---</dim>]` rendering exactly.
      sbVolBar.innerHTML =
        '<span class="vol-bracket">[</span>' +
        '<span class="vol-filled">' + "|".repeat(filled) + "</span>" +
        '<span class="vol-unfilled">' + "-".repeat(12 - filled) + "</span>" +
        '<span class="vol-bracket">]</span>';
    }
  }

  npVol.addEventListener("input", function () {
    const v = parseFloat(npVol.value);
    audio.volume = v;
    updateVolumeReadout(v);
  });
  // Initial volume readout
  updateVolumeReadout(parseFloat(npVol.value));

  // -------------------------------------------------------------------- //
  // Search input                                                         //
  // -------------------------------------------------------------------- //

  if (searchInput) {
    searchInput.addEventListener("input", onSearchInput);
  }

  // -------------------------------------------------------------------- //
  // Keyboard                                                             //
  // -------------------------------------------------------------------- //

  document.addEventListener("keydown", function (event) {
    const tag = (event.target && event.target.tagName) || "";
    const inField = tag === "INPUT" || tag === "TEXTAREA";

    // Slash always focuses search, even from an input — except when
    // already in the search input (to allow typing literal `/`).
    if (event.key === "/" && event.target !== searchInput) {
      event.preventDefault();
      searchInput?.focus();
      return;
    }
    if (event.key === "Escape") {
      if (lyricsPanel.classList.contains("is-open")) {
        lyricsPanel.classList.remove("is-open");
        event.preventDefault();
        return;
      }
      if (event.target === searchInput) {
        searchInput.blur();
        return;
      }
    }
    if (inField) return; // typing in an input — don't hijack other keys

    // Ignore modifier combos (Cmd/Ctrl/Alt + key). The command palette
    // owns Cmd/Ctrl+P; without this guard, Cmd+P would also fire the
    // bare-`p` "previous track" handler and skip the currently-playing
    // song the moment the palette opens. Bare keybinds (n/p/l/9/0/etc.)
    // are not intended to combine with modifiers, so a blanket bail is
    // correct here. (Shift is allowed — `<` and `>` are shifted.)
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.code === "Space") {
      event.preventDefault();
      togglePause();
    } else if (event.key === "n" || event.key === "N") {
      nextTrack();
    } else if (event.key === "p" || event.key === "P") {
      prevTrack();
    } else if (event.key === "r" || event.key === "R") {
      cycleRepeat();
    } else if (event.key === "s" || event.key === "S") {
      toggleShuffle();
    } else if (event.key === "l" || event.key === "L") {
      toggleLyrics();
    } else if (event.key === "0" || event.key === "+" || event.key === "=") {
      // Volume up — same keys as the TUI / mpv (`0` and `+`).
      event.preventDefault();
      adjustVolume(+0.05);
    } else if (event.key === "9" || event.key === "-") {
      event.preventDefault();
      adjustVolume(-0.05);
    } else if (event.key === "<" || event.key === ",") {
      // Seek backward — `<` is Shift+`,` on US layout; treat both the
      // shifted form and bare `,` as seek so layouts that don't shift
      // produce `<` still work.
      event.preventDefault();
      seekBy(-5);
    } else if (event.key === ">" || event.key === ".") {
      event.preventDefault();
      seekBy(+5);
    } else if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
      // `?` (Shift+`/`) opens the help overlay. Don't intercept the
      // bare `/` here — that path is handled above for search focus.
      event.preventDefault();
      toggleHelp();
    }
  });

  // -------------------------------------------------------------------- //
  // Help overlay (?)                                                     //
  // -------------------------------------------------------------------- //

  const helpPanel = document.getElementById("help-panel");
  const helpClose = document.getElementById("help-close");

  function toggleHelp() {
    if (!helpPanel) return;
    helpPanel.classList.toggle("is-open");
  }

  if (helpClose) {
    helpClose.addEventListener("click", () => {
      if (helpPanel) helpPanel.classList.remove("is-open");
    });
  }
  // Esc also closes help — extend the existing Escape branch above by
  // listening at module level since the existing branch returns early.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && helpPanel && helpPanel.classList.contains("is-open")) {
      helpPanel.classList.remove("is-open");
    }
  });
})();
