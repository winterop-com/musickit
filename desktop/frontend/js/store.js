// Host-agnostic persistence wrapper.
//
// Tauri exposes `window.__TAURI__.store.Store` (via the
// tauri-plugin-store crate). Electron's preload bridge installs
// `window.__musickitStore` over `electron-store`. Both expose the
// same async surface: get / set / delete / save.
//
// Two stores live alongside each other:
//   - servers store (.servers.json) — list of saved {host, user},
//     plus the most-recently-used pair so the login form can pre-fill
//   - session store (.session.json) — the active {host, user, token,
//     salt}; cleared on sign-out, used by the shell to re-create the
//     SubsonicClient on every cold launch without prompting again
//
// Why two: signing out should wipe credentials but keep the saved-server
// list intact. They have different lifecycles, so different files.

const STORE_FILE_SERVERS = ".servers.json";
const STORE_FILE_SESSION = ".session.json";

const KEY_SERVERS = "servers"; // [{host, user, last_used_at}]
const KEY_LAST_USED = "last_used"; // {host, user}
const KEY_SESSION = "session"; // {host, user, token, salt, server_type?}

/** Lazily resolve a host store handle for a given file. Null when no host. */
async function open(file) {
  const tauri = window.__TAURI__;
  if (tauri?.store?.Store) {
    return await tauri.store.Store.load(file);
  }
  // Electron preload installs a single store; pretend the same shim
  // satisfies both files (the on-disk shape is just key-value either way).
  if (window.__musickitStore) {
    return window.__musickitStore;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Saved-servers list — the login page's "remembered" dropdown.
// ---------------------------------------------------------------------------

export async function loadServers() {
  try {
    const store = await open(STORE_FILE_SERVERS);
    if (!store) return [];
    const raw = await store.get(KEY_SERVERS);
    if (!Array.isArray(raw)) return [];
    return raw.filter((s) => s && typeof s.host === "string" && typeof s.user === "string");
  } catch (e) {
    console.warn("loadServers failed:", e);
    return [];
  }
}

export async function loadLastUsed() {
  try {
    const store = await open(STORE_FILE_SERVERS);
    if (!store) return null;
    const v = await store.get(KEY_LAST_USED);
    if (!v || typeof v !== "object") return null;
    if (typeof v.host !== "string" || typeof v.user !== "string") return null;
    return v;
  } catch {
    return null;
  }
}

export async function rememberServer({ host, user }) {
  const store = await open(STORE_FILE_SERVERS);
  if (!store) return;
  const list = await loadServers();
  const now = new Date().toISOString();
  const existing = list.find((s) => s.host === host && s.user === user);
  if (existing) {
    existing.last_used_at = now;
  } else {
    list.push({ host, user, last_used_at: now });
  }
  list.sort((a, b) => (b.last_used_at || "").localeCompare(a.last_used_at || ""));
  await store.set(KEY_SERVERS, list);
  await store.set(KEY_LAST_USED, { host, user });
  await store.save();
}

export async function forgetServer({ host, user }) {
  const store = await open(STORE_FILE_SERVERS);
  if (!store) return;
  const list = (await loadServers()).filter((s) => !(s.host === host && s.user === user));
  await store.set(KEY_SERVERS, list);
  const last = await loadLastUsed();
  if (last && last.host === host && last.user === user) {
    await store.delete(KEY_LAST_USED);
  }
  await store.save();
}

// ---------------------------------------------------------------------------
// Session — the active credentials. Cleared on sign-out.
// ---------------------------------------------------------------------------

export async function loadSession() {
  try {
    const store = await open(STORE_FILE_SESSION);
    if (!store) return null;
    const v = await store.get(KEY_SESSION);
    if (!v || typeof v !== "object") return null;
    const { host, user, token, salt } = v;
    if (
      typeof host !== "string" ||
      typeof user !== "string" ||
      typeof token !== "string" ||
      typeof salt !== "string"
    ) {
      return null;
    }
    return v;
  } catch {
    return null;
  }
}

export async function saveSession(session) {
  const store = await open(STORE_FILE_SESSION);
  if (!store) return;
  await store.set(KEY_SESSION, session);
  await store.save();
}

export async function clearSession() {
  const store = await open(STORE_FILE_SESSION);
  if (!store) return;
  await store.delete(KEY_SESSION);
  await store.save();
}
