// Electron main process for the design-v2 wrapper — opens a single
// BrowserWindow pointed at `desktop/react/index.html` (the Claude
// Designer React prototype). The legacy Electron build lives at
// `desktop/electron/` and continues to ship the working vanilla-JS
// frontend; this folder is installed as a separate app so the two can
// run side-by-side while the redesign iterates.
//
// Window bounds persistence
// -------------------------
// `electron-window-state` v5 saved sub-min sizes during macOS minimize /
// fullscreen-exit, leaving every subsequent launch at a tiny window.
// We persist bounds ourselves to a separate electron-store file with
// explicit guards: never save while minimized or fullscreen, never save
// a size below the configured min (720x480), and on restore re-validate
// the size before applying. Fallback is the 1440x900 default below.
//
// What we deliberately do NOT do:
//   - Auto-open DevTools. Press Cmd+Option+I when needed; auto-opening
//     on every launch obscures the small login window and makes the
//     "is the app working?" question harder to answer at a glance.

const { app, BrowserWindow, screen } = require("electron");
const path = require("path");
// electron-store v8 needs `Store.initRenderer()` called once in the main
// process to register the IPC handler the renderer-side preload uses.
// Without it, the renderer's first `store.get(...)` call logs:
//   WebContents #1 called ipcRenderer.sendSync() with
//   'electron-store-get-data' channel without listeners
// and the call returns undefined.
const Store = require("electron-store");
Store.initRenderer();

const MIN_WIDTH = 720;
const MIN_HEIGHT = 480;
const DEFAULT_WIDTH = 1440;
const DEFAULT_HEIGHT = 900;
const SAVE_DEBOUNCE_MS = 250;

// Separate store from the servers / session files so a corrupt window
// state can never wedge the login flow.
const windowStore = new Store({ name: "musickit-window" });

let mainWindow;

function loadBounds() {
  const b = windowStore.get("bounds");
  if (!b || typeof b !== "object") return null;
  if (typeof b.width !== "number" || typeof b.height !== "number") return null;
  if (b.width < MIN_WIDTH || b.height < MIN_HEIGHT) return null;
  return b;
}

// Verify the saved (x, y) still lands on a connected display. If the
// user unplugged the monitor the window was on, fall back to centering.
function isVisibleOnAnyDisplay(b) {
  if (typeof b.x !== "number" || typeof b.y !== "number") return false;
  const rect = { x: b.x, y: b.y, width: b.width, height: b.height };
  return screen.getAllDisplays().some((d) => {
    const wa = d.workArea;
    return (
      rect.x < wa.x + wa.width &&
      rect.x + rect.width > wa.x &&
      rect.y < wa.y + wa.height &&
      rect.y + rect.height > wa.y
    );
  });
}

function saveBounds() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized() || mainWindow.isFullScreen()) return;
  const b = mainWindow.getBounds();
  if (b.width < MIN_WIDTH || b.height < MIN_HEIGHT) return;
  windowStore.set("bounds", b);
}

let saveTimer;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveBounds, SAVE_DEBOUNCE_MS);
}

function createWindow() {
  const saved = loadBounds();
  const usePosition = saved && isVisibleOnAnyDisplay(saved);

  mainWindow = new BrowserWindow({
    width: saved?.width ?? DEFAULT_WIDTH,
    height: saved?.height ?? DEFAULT_HEIGHT,
    x: usePosition ? saved.x : undefined,
    y: usePosition ? saved.y : undefined,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    center: !usePosition,
    title: "MusicKit Design",
    icon: path.join(__dirname, "..", "..", "tauri", "src-tauri", "icons", "icon.png"),
    // Hide the native title-bar text so our in-app topbar IS the title
    // bar — Spotify / Linear / Notion / VSCode all do this. On macOS,
    // `hiddenInset` keeps the traffic-light buttons in their usual top-
    // left position but drops the duplicated "MusicKit" label. CSS in
    // `_app.css` adds ~78px of left padding to `.topbar` on darwin so
    // the search input doesn't sit under the traffic lights, and
    // marks the bar as `-webkit-app-region: drag` so it functions as
    // a drag handle like a native title bar.
    titleBarStyle: "hiddenInset",
    // Windows / Linux equivalent — overlay buttons over the topbar
    // colour so the visual cue is consistent across platforms.
    titleBarOverlay: {
      color: "#1a1b26",
      symbolColor: "#a9b1d6",
      height: 36,
    },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload uses electron-store which needs require()
    },
  });

  mainWindow.on("resize", scheduleSave);
  mainWindow.on("move", scheduleSave);
  mainWindow.on("close", saveBounds);

  mainWindow.loadFile(path.join(__dirname, "..", "..", "react", "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // macOS convention: keep the app alive when the last window closes
  // so users reopen via the dock; quit on other platforms.
  if (process.platform !== "darwin") app.quit();
});
