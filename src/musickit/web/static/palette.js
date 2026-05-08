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

  const backdrop = document.getElementById("palette-backdrop");
  const input = document.getElementById("palette-input");
  const list = document.getElementById("palette-list");
  if (!backdrop || !input || !list) return;

  // Static command catalog. Label is the user-visible string, hint is
  // the keybind shown on the right, action is what happens on invoke.
  const COMMANDS = [
    { label: "Play / pause", hint: "space", action: { key: " " } },
    { label: "Next track", hint: "n", action: { key: "n" } },
    { label: "Previous track", hint: "p", action: { key: "p" } },
    { label: "Toggle lyrics", hint: "l", action: { key: "l" } },
    { label: "Toggle visualizer", hint: "f", action: { key: "f" } },
    { label: "Focus search", hint: "/", action: { key: "/" } },
    { label: "Close lyrics / visualizer", hint: "esc", action: { key: "Escape" } },
    { label: "Sign out", hint: "", action: { submit: "form.logout-form" } },
  ];

  let filtered = COMMANDS.slice();
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
    const needle = q.trim().toLowerCase();
    if (!needle) {
      filtered = COMMANDS.slice();
    } else {
      // Multi-token AND substring match — same UX as the TUI's `/` filter.
      const tokens = needle.split(/\s+/);
      filtered = COMMANDS.filter((cmd) => {
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
    if (key.length === 1) return "Key" + key.toUpperCase();
    return key;
  }

  function invoke(cmd) {
    close();
    if (cmd.action.key) {
      // Brief defer so the close finishes before the keybind handler
      // re-evaluates focus (e.g. `/` should focus the search bar).
      setTimeout(() => dispatchKey(cmd.action.key), 0);
    } else if (cmd.action.submit) {
      const form = document.querySelector(cmd.action.submit);
      if (form && typeof form.submit === "function") form.submit();
    }
  }

  input.addEventListener("input", () => applyFilter(input.value));

  input.addEventListener("keydown", (event) => {
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
  });

  list.addEventListener("click", (event) => {
    const li = event.target.closest("[data-idx]");
    if (!li) return;
    const cmd = filtered[parseInt(li.dataset.idx, 10)];
    if (cmd) invoke(cmd);
  });

  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });

  document.addEventListener("keydown", (event) => {
    // Ctrl+P (Linux/Win) or Cmd+P (macOS).
    const isPaletteShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p";
    if (isPaletteShortcut) {
      event.preventDefault();
      if (backdrop.hidden) {
        open();
      } else {
        close();
      }
    }
  });
})();
