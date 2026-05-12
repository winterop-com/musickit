// Preload script — runs before the renderer page loads, with access to
// Node + Electron APIs. Historically exposed a `window.__musickitStore`
// surface for the legacy vanilla-JS picker; the React frontend at
// `desktop/react/` uses localStorage directly and doesn't need it, but
// we keep the bridge in place in case future native features (Now
// Playing widget, OS notifications) want a backchannel to the main
// process. Removing it would also mean dropping `electron-store` from
// package.json — straightforward but separate from the design-v2
// retirement of the legacy UI.

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
