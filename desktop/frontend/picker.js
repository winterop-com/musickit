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

  /** Probe the server: confirm Subsonic + MusicKit's /web before navigating.
   *
   * Two probes:
   *   1. `<URL>/rest/ping?...&f=json` — Subsonic spec endpoint. Returns
   *      a subsonic-response envelope with `status: "ok"` (auth happy)
   *      or `status: "failed"` + error code 40 (auth required). Either
   *      response shape proves the URL IS a Subsonic server.
   *   2. `<URL>/web` — confirms MusicKit specifically. Other Subsonic
   *      servers (Navidrome, Airsonic) return 404 here. MusicKit
   *      either returns the HTML or a 303 redirect to /login.
   *
   * On success we navigate to /web (which auto-redirects to /login if
   * no session cookie is present yet). On failure we surface a
   * distinct error per cause so the user knows what's wrong.
   */
  async function probeAndConnect(url) {
    setStatus(`Probing ${url}…`);
    // Step 1: Subsonic-spec ping. Throwaway creds — we only care about
    // the response shape, not whether they auth correctly.
    const pingUrl =
      url +
      "/rest/ping?u=probe&p=probe&v=1.16.1&c=musickit-desktop&f=json";
    let pingBody;
    try {
      const resp = await fetch(pingUrl, { method: "GET", credentials: "omit" });
      if (!resp.ok && resp.status !== 401) {
        // 4xx/5xx that isn't 401 — server is reachable but isn't speaking Subsonic.
        throw new Error(`HTTP ${resp.status}`);
      }
      pingBody = await resp.json();
    } catch (e) {
      const msg = e?.message || String(e);
      if (msg === "Failed to fetch") {
        setStatus(`Could not reach ${url} — is the server running?`, { error: true });
      } else {
        setStatus(`${url} doesn't speak Subsonic (${msg})`, { error: true });
      }
      return false;
    }
    if (!pingBody || !pingBody["subsonic-response"]) {
      setStatus(`${url} responded but isn't a Subsonic-compatible server.`, {
        error: true,
      });
      return false;
    }
    // Step 2: confirm the MusicKit web UI specifically. Non-MusicKit
    // Subsonic servers (Navidrome / Airsonic / etc.) return 404 here.
    setStatus("Subsonic server reached — checking for MusicKit UI…");
    try {
      const resp = await fetch(url + "/web", {
        method: "GET",
        credentials: "omit",
        redirect: "manual", // /web 303-redirects to /login when not authed; both are fine
      });
      const ok = resp.ok || resp.status === 303 || resp.type === "opaqueredirect";
      if (!ok) {
        setStatus(
          `Subsonic server detected, but it doesn't have the MusicKit web UI ` +
            `(this desktop app currently only works against musickit serve).`,
          { error: true },
        );
        return false;
      }
    } catch (e) {
      // Some webviews surface CORS / redirect probes differently; treat
      // a fetch error here as "probably MusicKit" and try anyway.
      console.warn("/web probe inconclusive; navigating optimistically:", e);
    }
    setStatus(`Connecting to ${url}…`);
    window.location.href = url + "/web";
    return true;
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
      connect.addEventListener("click", async () => {
        connect.disabled = true;
        const ok = await probeAndConnect(s.url);
        if (ok) {
          await saveServer(s.url);
        }
        connect.disabled = false;
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
    const button = event.target.querySelector('button[type="submit"]');
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
    if (button) button.disabled = true;
    const ok = await probeAndConnect(url);
    if (ok) {
      try {
        await saveServer(url);
      } catch (e) {
        console.warn("saveServer failed:", e);
      }
    }
    if (button) button.disabled = false;
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
