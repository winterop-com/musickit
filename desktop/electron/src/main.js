// Electron main process — opens a single BrowserWindow pointed at the
// shared `desktop/frontend/index.html`. The renderer page (the SPA) is
// identical to what Tauri loads; the only Electron-specific bits live
// in this file + `preload.js`.
//
// What we deliberately do NOT do:
//   - Auto-open DevTools. Press Cmd+Option+I when needed; auto-opening
//     on every launch obscures the small login window and makes the
//     "is the app working?" question harder to answer at a glance.
//   - Use `electron-window-state`. It saved sub-min sizes for us during
//     testing, leaving every subsequent launch at a tiny window. The
//     hardcoded 1440x900 default is at least predictable; we'll re-add
//     window-state once we trust the saved-state file.

const { app, BrowserWindow } = require("electron");
const path = require("path");

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 720,
    minHeight: 480,
    center: true,
    title: "MusicKit",
    icon: path.join(__dirname, "..", "..", "tauri", "src-tauri", "icons", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload uses electron-store which needs require()
    },
  });

  mainWindow.loadFile(path.join(__dirname, "..", "..", "frontend", "index.html"));
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
