// MusicKit Desktop — entry point.
//
// Boots into either a login page or the main shell based on whether
// the host store has an active session. After a successful login the
// shell renders; on sign-out we wipe the session and remount the login
// page.
//
// Everything is rendered client-side from `desktop/frontend/`. The
// underlying server is any spec-compliant Subsonic instance — we never
// assume the server has the MusicKit web UI.

import { tryLogin, subsonicClient, SubsonicError } from "./api.js";
import {
  loadSession,
  saveSession,
  clearSession,
  loadLastUsed,
  loadServers,
  rememberServer,
  forgetServer,
} from "./store.js";
import { renderShell } from "./shell.js";

const ROOT_ID = "root";

/** Top-level state machine. Either renders login or shell. */
async function boot() {
  const session = await loadSession();
  if (!session) {
    mountLogin();
    return;
  }
  // Verify the server is reachable before showing the shell. Without
  // this check, a stale session against a server that's offline (laptop
  // moved off the LAN, server stopped, Tailscale down) renders the full
  // empty shell for ~2s while the artist-list fetch hangs, then loads
  // into a broken state. Pre-check with a short ping and fall back to
  // the login form (pre-filled with the last-used host + user) when it
  // fails, so the user can either re-enter their password against a
  // now-reachable server or switch to a different one.
  const client = subsonicClient(session);
  try {
    await pingWithTimeout(client, 3000);
  } catch (e) {
    console.warn("session restore: server unreachable, showing login:", e);
    mountLogin({
      host: session.host,
      user: session.user,
      connectError: `Couldn't reach ${session.host}. Check the URL or the server, then sign in again.`,
    });
    return;
  }
  mountShell(client, session);
}

/**
 * Race `client.query("ping")` against a hard timeout. Returns the
 * server-info envelope on success, throws on either failure path
 * (network error or timeout).
 */
async function pingWithTimeout(client, timeoutMs) {
  return Promise.race([
    client.query("ping"),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`ping timeout after ${timeoutMs}ms`)), timeoutMs),
    ),
  ]);
}

async function mountLogin(prefill = null) {
  const root = document.getElementById(ROOT_ID);
  if (!root) return;
  const last = prefill ?? (await loadLastUsed());
  const saved = await loadServers();

  root.innerHTML = `
    <main class="login-shell">
      <header class="login-brand">
        <span class="brand-name">MusicKit</span>
        <span class="brand-tag">desktop</span>
      </header>

      <section class="login-card">
        <h1 class="login-title">Connect to a Subsonic server</h1>
        <p class="login-hint">
          Works against any spec-compliant server: <code>musickit serve</code>,
          Navidrome, Airsonic, Gonic, etc.
        </p>

        <form class="login-form" id="login-form" autocomplete="off">
          <label class="login-field">
            <span>Server URL</span>
            <input type="url" id="login-host" autocomplete="off" spellcheck="false"
                   placeholder="http://hostname:4533" required>
          </label>
          <label class="login-field">
            <span>Username</span>
            <input type="text" id="login-user" autocomplete="username"
                   spellcheck="false" required>
          </label>
          <label class="login-field">
            <span>Password</span>
            <input type="password" id="login-pass" autocomplete="current-password" required>
          </label>
          <button type="submit" class="login-submit" id="login-submit">Connect</button>
        </form>

        <p class="login-status" id="login-status" hidden></p>

        ${
          saved.length > 0
            ? `<section class="saved-servers">
                 <h2 class="saved-heading">Saved servers</h2>
                 <ul class="saved-list" id="saved-list"></ul>
               </section>`
            : ""
        }
      </section>
    </main>
  `;

  // Pre-fill order of precedence:
  //   1. `?host=&user=&password=` query string — `musickit ui --url ...`
  //      passes these in so the form opens already populated. Useful for
  //      a one-command "open the UI pointed at this server" workflow.
  //   2. Last-used credentials from the store.
  //   3. The local-musickit-serve defaults — most first-time users are
  //      testing against `musickit serve <library>` on their own machine.
  const params = new URLSearchParams(window.location.search);
  const qHost = params.get("host");
  const qUser = params.get("user");
  const qPass = params.get("password");
  const hostInput = document.getElementById("login-host");
  const userInput = document.getElementById("login-user");
  const passInput = document.getElementById("login-pass");
  if (hostInput) hostInput.value = qHost || last?.host || "http://localhost:4533";
  if (userInput) userInput.value = qUser || last?.user || "admin";
  if (passInput && qPass) passInput.value = qPass;
  else if (passInput && !last) passInput.value = "admin";

  // If `boot()` couldn't reach the previously-active server, surface
  // that as an error message above the form so the user knows why
  // they landed back at the login screen.
  if (prefill?.connectError) {
    const status = document.getElementById("login-status");
    setStatus(status, prefill.connectError, true);
  }

  // Render saved-servers list.
  if (saved.length > 0) {
    const list = document.getElementById("saved-list");
    if (list) {
      list.innerHTML = "";
      for (const s of saved) {
        const li = document.createElement("li");
        li.className = "saved-row";
        li.innerHTML = `
          <span class="saved-host">${escapeHtml(s.host)}</span>
          <span class="saved-user">${escapeHtml(s.user)}</span>
          <button type="button" class="saved-action saved-pick">Use</button>
          <button type="button" class="saved-action saved-forget danger">Forget</button>
        `;
        const pickBtn = li.querySelector(".saved-pick");
        const forgetBtn = li.querySelector(".saved-forget");
        pickBtn?.addEventListener("click", () => {
          const hostInput = document.getElementById("login-host");
          const userInput = document.getElementById("login-user");
          const passInput = document.getElementById("login-pass");
          if (hostInput) hostInput.value = s.host;
          if (userInput) userInput.value = s.user;
          if (passInput) passInput.focus();
        });
        forgetBtn?.addEventListener("click", async () => {
          await forgetServer({ host: s.host, user: s.user });
          mountLogin(prefill);
        });
        list.appendChild(li);
      }
    }
  }

  // Focus password (host + user are pre-filled to sane defaults so the
  // password is the next thing the user actually needs to type — or
  // press Enter, if first-run defaults are admin/admin against a local
  // server).
  passInput?.focus();

  document.getElementById("login-form")?.addEventListener("submit", onLoginSubmit);
}

async function onLoginSubmit(event) {
  event.preventDefault();
  const submit = document.getElementById("login-submit");
  const status = document.getElementById("login-status");
  const hostRaw = document.getElementById("login-host")?.value || "";
  const user = (document.getElementById("login-user")?.value || "").trim();
  const password = document.getElementById("login-pass")?.value || "";

  const host = normaliseHost(hostRaw);
  if (!host) {
    setStatus(status, "Enter a server URL like http://localhost:4533", true);
    return;
  }
  if (!user) {
    setStatus(status, "Enter a username", true);
    return;
  }
  if (!password) {
    setStatus(status, "Enter a password", true);
    return;
  }

  if (submit) submit.disabled = true;
  setStatus(status, `Connecting to ${host}…`, false);
  try {
    const { client, serverInfo } = await tryLogin({ host, user, password });
    const session = {
      host: client.host,
      user: client.user,
      token: client.token,
      salt: client.salt,
      server_type: serverInfo.type,
      server_version: serverInfo.serverVersion,
    };
    await saveSession(session);
    await rememberServer({ host: client.host, user });
    setStatus(status, `Connected to ${serverInfo.type}. Loading…`, false);
    mountShell(client, session);
  } catch (e) {
    if (e instanceof SubsonicError) {
      if (e.code === 40) {
        setStatus(status, "Wrong username or password.", true);
      } else if (e.code === 41) {
        setStatus(status, "Server doesn't accept token authentication.", true);
      } else {
        setStatus(status, e.subsonicMessage || e.message, true);
      }
    } else {
      const msg = e?.message || String(e);
      if (msg.includes("network error")) {
        setStatus(status, `Could not reach ${host}. Is the server running?`, true);
      } else if (msg.includes("not a Subsonic envelope")) {
        setStatus(status, `${host} responded but isn't a Subsonic-compatible server.`, true);
      } else {
        setStatus(status, msg, true);
      }
    }
    if (submit) submit.disabled = false;
  }
}

function mountShell(client, session) {
  const root = document.getElementById(ROOT_ID);
  if (!root) return;
  renderShell(root, client, session, {
    onSignOut: async () => {
      await clearSession();
      mountLogin();
    },
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normaliseHost(raw) {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  if (!/^https?:\/\//i.test(trimmed)) return "http://" + trimmed;
  return trimmed;
}

function setStatus(el, msg, isError) {
  if (!el) return;
  el.textContent = msg || "";
  el.hidden = !msg;
  el.classList.toggle("is-error", !!isError);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
