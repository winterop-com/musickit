// Ctrl+P / Cmd+P command palette — Textual-inspired.
//
// A small modal that lets the user invoke any keybind action via fuzzy
// substring search. Implemented as a self-contained module: registers
// commands once, hooks the keybind, dispatches on Enter.
//
// Commands map to one of two effectors:
//   - `key` : synthesise the keydown handler the global app.js
//     listens to (e.g. {key: "n"} fires the n=Next handler).
//   - `click` : click an element by id (e.g. play-button).
//
// New commands plug in by extending the COMMANDS array below.

(function () {
  "use strict";

  // The palette DOM is rendered by `shell.js`'s `renderShell()` AFTER
  // login — when this script runs at page load the DOM doesn't exist
  // yet. Lazy-resolve refs on first Cmd+P press, then wire all the
  // per-palette listeners (input, list, backdrop) once. The global
  // Cmd+P keydown listener is the only thing installed at load time.
  let backdrop = null;
  let input = null;
  let list = null;
  let wired = false;

  function wireRefs() {
    backdrop = document.getElementById("palette-backdrop");
    input = document.getElementById("palette-input");
    list = document.getElementById("palette-list");
    if (!backdrop || !input || !list) return false;
    if (wired) return true;
    wired = true;
    input.addEventListener("input", () => applyFilter(input.value));
    input.addEventListener("keydown", onInputKeydown);
    list.addEventListener("click", onListClick);
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) close();
    });
    return true;
  }

  // Static command catalog. Label is the user-visible string, hint is
  // the keybind shown on the right, action is what happens on invoke.
  // `modes`: which playback contexts the command applies to. Omit for
  // "all modes". Modes: "track" (album track playing or idle), "radio"
  // (radio stream playing). The palette filters by current mode at
  // open time so radio listeners don't see Repeat / Shuffle / Seek
  // commands that don't apply to live streams.
  const COMMANDS = [
    { label: "Play / pause", hint: "space", action: { key: " " } },
    { label: "Next track", hint: "n", action: { key: "n" }, modes: ["track"] },
    { label: "Previous track", hint: "p", action: { key: "p" }, modes: ["track"] },
    { label: "Volume up", hint: "0", action: { key: "0" } },
    { label: "Volume down", hint: "9", action: { key: "9" } },
    { label: "Seek backward 5s", hint: "<", action: { key: "<" }, modes: ["track"] },
    { label: "Seek forward 5s", hint: ">", action: { key: ">" }, modes: ["track"] },
    { label: "Cycle repeat (off / album / track)", hint: "r", action: { key: "r" }, modes: ["track"] },
    { label: "Toggle shuffle", hint: "s", action: { key: "s" }, modes: ["track"] },
    { label: "Toggle lyrics", hint: "l", action: { key: "l" }, modes: ["track"] },
    { label: "Toggle visualizer", hint: "f", action: { key: "f" } },
    { label: "Focus search", hint: "/", action: { key: "/" } },
    { label: "Show keyboard shortcuts", hint: "?", action: { key: "?" } },
    { label: "Close lyrics / visualizer", hint: "esc", action: { key: "Escape" } },
    { label: "Sign out", hint: "", action: { click: "#signout-btn" } },
  ];

  function currentMode() {
    // Mirrors the `body.is-radio` class set by app.js when the user
    // loads the radio panel or plays a station.
    return document.body.classList.contains("is-radio") ? "radio" : "track";
  }

  function applicableCommands() {
    const mode = currentMode();
    return COMMANDS.filter((cmd) => !cmd.modes || cmd.modes.includes(mode));
  }

  let filtered = applicableCommands();
  let activeIdx = 0;

  function render() {
    list.innerHTML = filtered
      .map((cmd, i) => {
        const cls = i === activeIdx ? "palette-item is-active" : "palette-item";
        const hint = cmd.hint ? `<kbd class="palette-hint">${escapeHtml(cmd.hint)}</kbd>` : "";
        return `<li class="${cls}" role="option" data-idx="${i}">
          <span class="palette-label">${escapeHtml(cmd.label)}</span>${hint}
        </li>`;
      })
      .join("");
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function applyFilter(q) {
    const pool = applicableCommands(); // re-evaluate mode each open / keystroke
    const needle = q.trim().toLowerCase();
    if (!needle) {
      filtered = pool;
    } else {
      // Multi-token AND substring match — same UX as the TUI's `/` filter.
      const tokens = needle.split(/\s+/);
      filtered = pool.filter((cmd) => {
        const hay = (cmd.label + " " + cmd.hint).toLowerCase();
        return tokens.every((t) => hay.includes(t));
      });
    }
    activeIdx = 0;
    render();
  }

  function open() {
    backdrop.hidden = false;
    input.value = "";
    applyFilter("");
    // Defer focus so Chrome doesn't fight our keydown event.
    requestAnimationFrame(() => input.focus());
  }

  function close() {
    backdrop.hidden = true;
  }

  function dispatchKey(key) {
    // Fire a keydown the app.js + visualizer.js listeners pick up.
    // `bubbles: true` so document-level handlers see it.
    const event = new KeyboardEvent("keydown", {
      key,
      code: keyToCode(key),
      bubbles: true,
      cancelable: true,
    });
    document.dispatchEvent(event);
  }

  function keyToCode(key) {
    if (key === " ") return "Space";
    if (key === "/") return "Slash";
    if (key === "Escape") return "Escape";
    if (key === "<" || key === ",") return "Comma";
    if (key === ">" || key === ".") return "Period";
    if (key === "0" || key === "9") return "Digit" + key;
    if (key === "?") return "Slash"; // Shift+/
    if (key.length === 1) return "Key" + key.toUpperCase();
    return key;
  }

  function dispatchKeyExtended(key) {
    // Some keys ride on Shift (`?`, `<`, `>`); fire the event with the
    // proper modifier so the listener's `event.key` checks (which look
    // at the produced character) match.
    const shifted = key === "?" || key === "<" || key === ">";
    const event = new KeyboardEvent("keydown", {
      key,
      code: keyToCode(key),
      shiftKey: shifted,
      bubbles: true,
      cancelable: true,
    });
    document.dispatchEvent(event);
  }

  function invoke(cmd) {
    close();
    if (cmd.action.key) {
      // Brief defer so the close finishes before the keybind handler
      // re-evaluates focus (e.g. `/` should focus the search bar).
      setTimeout(() => dispatchKeyExtended(cmd.action.key), 0);
    } else if (cmd.action.click) {
      // Click a button by selector — used for Sign out (the desktop's
      // signout button calls `hooks.onSignOut`, no form submit needed).
      const el = document.querySelector(cmd.action.click);
      if (el && typeof el.click === "function") el.click();
    } else if (cmd.action.submit) {
      const form = document.querySelector(cmd.action.submit);
      if (form && typeof form.submit === "function") form.submit();
    }
  }

  function onInputKeydown(event) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIdx = Math.min(activeIdx + 1, filtered.length - 1);
      render();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIdx = Math.max(activeIdx - 1, 0);
      render();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const cmd = filtered[activeIdx];
      if (cmd) invoke(cmd);
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  function onListClick(event) {
    const li = event.target.closest("[data-idx]");
    if (!li) return;
    const cmd = filtered[parseInt(li.dataset.idx, 10)];
    if (cmd) invoke(cmd);
  }

  document.addEventListener("keydown", (event) => {
    // Ctrl+P (Linux/Win) or Cmd+P (macOS).
    const isPaletteShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p";
    if (!isPaletteShortcut) return;
    if (!wireRefs()) return; // no palette DOM on this page (login screen)
    event.preventDefault();
    if (backdrop.hidden) {
      open();
    } else {
      close();
    }
  });
})();
