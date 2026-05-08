// Electron main process — opens a single BrowserWindow pointed at the
// shared `desktop/frontend/index.html` picker. The picker page reads /
// writes a server-URL list via `window.__musickitStore` (a shim that
// preload.js installs on top of `electron-store`), so its source is
// 100% identical to the version Tauri runs against.
//
// Phase 1 MVP: parity-with-Tauri features only.
//   - URL picker → navigate webContents to <URL>/web
//   - Persistent server-URL store
//   - Window state restored across launches (electron-window-state)
//
// Native polish (MediaSession metadata, global media keys, mDNS LAN
// discovery, dock badge) lives in Phase 2 — kept symmetrical between
// the Tauri and Electron implementations so the comparison is clean.

const { app, BrowserWindow, session } = require("electron");
const path = require("path");
const Store = require("electron-store").default || require("electron-store");
const windowStateKeeper = require("electron-window-state");

// Persistence file is named identically to Tauri's so a user could in
// theory move between the two without re-typing their server list,
// though the on-disk format is electron-store's JSON (compatible with
// tauri-plugin-store's JSON when both store the same shape).
const SERVERS_STORE = ".servers";

let mainWindow;

function createWindow() {
  const winState = windowStateKeeper({
    defaultWidth: 1440,
    defaultHeight: 900,
  });

  mainWindow = new BrowserWindow({
    x: winState.x,
    y: winState.y,
    width: winState.width,
    height: winState.height,
    minWidth: 720,
    minHeight: 480,
    title: "MusicKit",
    icon: path.join(__dirname, "..", "..", "tauri", "src-tauri", "icons", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload uses electron-store which needs require()
    },
  });

  winState.manage(mainWindow);

  // Open devtools in dev (no production build distinction yet — all
  // current launches are dev).
  if (process.env.NODE_ENV !== "production") {
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  // The picker shipped under desktop/frontend/.
  mainWindow.loadFile(path.join(__dirname, "..", "..", "frontend", "index.html"));
}

// Restrict cookies / cache to a separate partition so the Electron
// session is isolated from the system browser. Cookies set by the
// musickit serve login form land here and persist across launches.
app.whenReady().then(() => {
  // The default session is fine for now; named here for explicitness in
  // case we want a custom one later (e.g. per-server partitions for
  // multi-server switching).
  session.defaultSession;
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // macOS convention: keep the app running when the last window closes
  // so users can reopen via the dock; quit elsewhere.
  if (process.platform !== "darwin") app.quit();
});

// Expose the store via the preload bridge — no IPC needed because
// `electron-store` works directly in the renderer once `nodeIntegration`
// is off and `contextIsolation` is on, so we proxy from preload.
module.exports = { SERVERS_STORE, Store };
