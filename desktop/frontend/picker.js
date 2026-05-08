// Server picker — runs in the desktop wrapper's webview before
// connecting to a `musickit serve` instance. Persists "saved servers"
// via the host's storage plugin so the user picks a remembered URL on
// subsequent launches without re-typing.
//
// Phase 1 keeps it simple: enter URL + click Connect → navigate the
// same webview to <URL>/web. Cookies set by the server's login form
// persist across launches in the webview's cookie jar, so subsequent
// connections to the same URL skip straight to /web.
//
// Storage is host-agnostic — looks up the Tauri 2 globals first, then
// falls back to a `window.__musickitStore` shim that an Electron
// sibling can install via a preload script. Both implementations share
// the same `.get(key) / .set(key, val) / .delete(key) / .save()`
// surface so this picker doesn't need to know which host is rendering it.

(function () {
  "use strict";

  const STORE_FILE = ".servers.json";
  const STORE_KEY = "servers"; // [{url, last_used_at}]
  const LAST_KEY = "last_used"; // url string

  /** Lazily resolve the host store. Returns null if no host is detected. */
  async function getStore() {
    const tauri = window.__TAURI__;
    if (tauri?.store?.Store) {
      return await tauri.store.Store.load(STORE_FILE);
    }
    if (window.__musickitStore) {
      return window.__musickitStore;
    }
    return null;
  }

  async function loadServers() {
    try {
      const store = await getStore();
      if (!store) return [];
      const raw = await store.get(STORE_KEY);
      if (!Array.isArray(raw)) return [];
      return raw.filter((s) => s && typeof s.url === "string");
    } catch (e) {
      console.warn("loadServers failed:", e);
      return [];
    }
  }

  async function loadLastUsed() {
    try {
      const store = await getStore();
      if (!store) return null;
      const v = await store.get(LAST_KEY);
      return typeof v === "string" ? v : null;
    } catch {
      return null;
    }
  }

  async function saveServer(url) {
    const store = await getStore();
    if (!store) return;
    const list = await loadServers();
    const now = new Date().toISOString();
    const existing = list.find((s) => s.url === url);
    if (existing) {
      existing.last_used_at = now;
    } else {
      list.push({ url, last_used_at: now });
    }
    list.sort((a, b) => (b.last_used_at || "").localeCompare(a.last_used_at || ""));
    await store.set(STORE_KEY, list);
    await store.set(LAST_KEY, url);
    await store.save();
  }

  async function removeServer(url) {
    const store = await getStore();
    if (!store) return;
    const list = (await loadServers()).filter((s) => s.url !== url);
    await store.set(STORE_KEY, list);
    const last = await loadLastUsed();
    if (last === url) {
      await store.delete(LAST_KEY);
    }
    await store.save();
  }

  /** Trim, drop trailing slashes, default scheme to http://. */
  function normaliseUrl(raw) {
    const trimmed = (raw || "").trim().replace(/\/+$/, "");
    if (!trimmed) return "";
    if (!/^https?:\/\//i.test(trimmed)) return "http://" + trimmed;
    return trimmed;
  }

  function validateUrl(s) {
    const u = new URL(s);
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      throw new Error("Only http and https URLs are supported.");
    }
  }

  function setStatus(msg, opts = {}) {
    const el = document.getElementById("picker-status");
    if (!el) return;
    el.textContent = msg || "";
    el.hidden = !msg;
    el.classList.toggle("is-error", !!opts.error);
  }

  /** Hop the webview to the server's /web entry point. */
  function navigateToServer(url) {
    setStatus(`Connecting to ${url}…`);
    window.location.href = url + "/web";
  }

  function renderSavedList(servers, lastUsed) {
    const wrap = document.getElementById("picker-saved");
    const list = document.getElementById("saved-list");
    if (!wrap || !list) return;
    list.innerHTML = "";
    if (servers.length === 0) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    for (const s of servers) {
      const li = document.createElement("li");
      li.className = "saved-row";
      const url = document.createElement("span");
      url.className = "saved-url";
      url.textContent = s.url + (s.url === lastUsed ? "  (last used)" : "");
      const connect = document.createElement("button");
      connect.type = "button";
      connect.className = "saved-action";
      connect.textContent = "Connect";
      connect.addEventListener("click", () => {
        saveServer(s.url).finally(() => navigateToServer(s.url));
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "saved-action danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        await removeServer(s.url);
        const fresh = await loadServers();
        const lu = await loadLastUsed();
        renderSavedList(fresh, lu);
      });
      li.append(url, connect, remove);
      list.appendChild(li);
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    const input = document.getElementById("server-url");
    const url = normaliseUrl(input?.value);
    if (!url) {
      setStatus("Enter a URL like http://localhost:4533", { error: true });
      return;
    }
    try {
      validateUrl(url);
    } catch (e) {
      setStatus(e.message || "Invalid URL", { error: true });
      return;
    }
    try {
      await saveServer(url);
    } catch (e) {
      console.warn("saveServer failed:", e);
    }
    navigateToServer(url);
  }

  async function init() {
    document.getElementById("picker-form")?.addEventListener("submit", onSubmit);
    const servers = await loadServers();
    const last = await loadLastUsed();
    renderSavedList(servers, last);
    const input = document.getElementById("server-url");
    if (input && last) {
      input.value = last;
    }
    input?.focus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
