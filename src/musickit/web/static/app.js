// musickit web UI — vanilla JS, no framework.
//
// What runs here:
//   - Click delegation on the three panes: artist row -> fetch fragment,
//     swap into albums-pane; album row -> fetch fragment, swap into
//     tracks-pane; track row -> set <audio src> and play.
//   - Keep the now-playing footer in sync with the <audio> element via
//     play / pause / timeupdate / ended events.
//   - Space + media keys for play/pause; same vibe as the TUI.
//
// State is kept on the elements themselves (data-* attributes) plus a
// tiny `state` object below for cross-pane bookkeeping. No reactivity
// layer — ~150 lines, plain reads from DOM.

(function () {
  "use strict";

  const audio = document.getElementById("audio");
  const playButton = document.getElementById("play-button");
  const npTitle = document.getElementById("np-title");
  const npArtist = document.getElementById("np-artist");
  const npPos = document.getElementById("np-pos");
  const npDur = document.getElementById("np-dur");
  const npBar = document.getElementById("np-bar");
  const npVol = document.getElementById("np-vol");

  const state = {
    currentTrackId: null,
    currentRowEl: null,
  };

  function fmtTime(seconds) {
    if (!isFinite(seconds) || seconds <= 0) return "00:00";
    const s = Math.floor(seconds);
    return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  }

  async function loadFragment(url, targetId) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      document.getElementById(targetId).innerHTML =
        `<p class="empty">Failed to load: ${response.status} ${response.statusText}</p>`;
      return;
    }
    document.getElementById(targetId).innerHTML = await response.text();
  }

  function markActiveRow(button, paneSelector) {
    document.querySelectorAll(paneSelector + " .row-button.is-active").forEach((el) => {
      el.classList.remove("is-active");
    });
    button.classList.add("is-active");
  }

  function playTrack(trackId, title, artist, rowEl) {
    audio.src = "/rest/stream?id=" + encodeURIComponent(trackId) + "&f=raw";
    audio.play().catch((err) => {
      // Browser autoplay policies can refuse first-play without user
      // gesture — but this IS a click handler, so this only fires on
      // genuinely-broken state (e.g. session expired -> 401). Surface it.
      console.warn("playback failed:", err);
    });
    state.currentTrackId = trackId;

    // Visual: orange-out the playing row, restore the previous one.
    if (state.currentRowEl) {
      state.currentRowEl.classList.remove("is-playing");
    }
    if (rowEl) {
      rowEl.classList.add("is-playing");
      state.currentRowEl = rowEl;
    }

    npTitle.textContent = title || "—";
    npArtist.textContent = artist || "";
    playButton.disabled = false;
  }

  function togglePause() {
    if (!state.currentTrackId) return;
    if (audio.paused) {
      audio.play();
    } else {
      audio.pause();
    }
  }

  // ----- click delegation ----------------------------------------------

  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "load-artist") {
      markActiveRow(button, ".pane-artists");
      loadFragment("/web/artist/" + encodeURIComponent(button.dataset.id), "albums-pane");
      // Reset tracks pane since the artist changed.
      document.getElementById("tracks-pane").innerHTML =
        '<p class="empty">Pick an album to see tracks.</p>';
    } else if (action === "load-album") {
      markActiveRow(button, ".pane-albums");
      loadFragment("/web/album/" + encodeURIComponent(button.dataset.id), "tracks-pane");
    } else if (action === "play-track") {
      playTrack(button.dataset.id, button.dataset.title, button.dataset.artist, button);
    } else if (action === "play-pause") {
      togglePause();
    }
  });

  // ----- audio element wiring ------------------------------------------

  audio.addEventListener("play", function () {
    playButton.classList.remove("is-paused");
    playButton.firstElementChild.textContent = "‖";
  });

  audio.addEventListener("pause", function () {
    playButton.classList.add("is-paused");
    playButton.firstElementChild.textContent = "▶";
  });

  audio.addEventListener("ended", function () {
    playButton.classList.remove("is-paused");
    playButton.firstElementChild.textContent = "▶";
  });

  audio.addEventListener("timeupdate", function () {
    npPos.textContent = fmtTime(audio.currentTime);
    if (audio.duration) {
      npDur.textContent = fmtTime(audio.duration);
      npBar.value = audio.currentTime / audio.duration;
    }
  });

  audio.addEventListener("loadedmetadata", function () {
    npDur.textContent = fmtTime(audio.duration);
  });

  // Click on the progress bar to seek.
  npBar.addEventListener("click", function (event) {
    if (!audio.duration) return;
    const rect = npBar.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * audio.duration;
  });

  // Volume slider.
  npVol.addEventListener("input", function () {
    audio.volume = parseFloat(npVol.value);
  });

  // ----- keyboard shortcuts --------------------------------------------

  document.addEventListener("keydown", function (event) {
    // Don't hijack typing in inputs / textareas.
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePause();
    }
  });
})();
