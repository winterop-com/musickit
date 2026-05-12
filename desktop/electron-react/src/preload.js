// Preload script — runs before the renderer page loads, with access to
// Node + Electron APIs. Installs `window.__musickitStore` so the
// shared picker (`desktop/frontend/picker.js`) gets the same async
// `.get / .set / .delete / .save` surface the Tauri build provides via
// `window.__TAURI__.store`.
//
// No IPC used because electron-store works synchronously on the
// renderer side via the `path` argument; we wrap the calls to expose
// the same async-flavour surface for picker.js's await calls.

const { contextBridge } = require("electron");
const ElectronStore = require("electron-store");

// Single store instance, scoped to a JSON file in app.getPath('userData').
// The picker uses generic .get('servers') / .set('servers', ...) so the
// schema mirrors the Tauri build.
const store = new ElectronStore({
  name: "musickit-servers",
  // No schema — picker will write whatever shape it wants. Validation
  // happens picker-side (filter to {url, last_used_at} entries).
});

contextBridge.exposeInMainWorld("__musickitStore", {
  get: async (key) => store.get(key),
  set: async (key, value) => {
    store.set(key, value);
  },
  delete: async (key) => {
    store.delete(key);
  },
  // electron-store writes synchronously on every set; .save() is a
  // no-op so the picker's await chain stays uniform with the Tauri
  // path (which DOES require an explicit save()).
  save: async () => {},
});
