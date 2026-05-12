//! MusicKit desktop entrypoint.
//!
//! The Rust side stays minimal — it registers plugins, runs the Tauri
//! app, and persists the main window's outer bounds across launches.
//! The entire UI (login, shell, browse, playback) is owned by
//! `desktop/react/` — the React + Babel-standalone client.
//!
//! Window bounds persistence
//! -------------------------
//! `tauri-plugin-window-state` v2 had a macOS bug where Resized events
//! delivered during minimize / fullscreen exit carried sub-min sizes
//! (we saw 1780x106 restored as the new "remembered" size). Rather than
//! pull that plugin back in, we store a single `window.json` file in
//! `app_data_dir()` ourselves and gate every save on:
//!
//!   - `is_minimized()` is false
//!   - `is_fullscreen()` is false
//!   - the size is at or above the logical 720x480 min (in physical px)
//!
//! On startup we apply the saved size/position only if the file parses
//! and the size still passes the same min check, so a corrupt or stale
//! file can never wedge the app at a tiny window. The fallback is the
//! 1440x900 default declared in `tauri.conf.json`.
//!
//! What we deliberately do NOT do:
//!
//!   - Auto-open DevTools. The user can press Cmd+Option+I (macOS)
//!     or right-click → Inspect Element when they want it. Auto-opening
//!     is jarring on every launch and the panel obscures the small
//!     login window.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{Manager, PhysicalPosition, PhysicalSize, WindowEvent};

const MIN_WIDTH_LOGICAL: f64 = 720.0;
const MIN_HEIGHT_LOGICAL: f64 = 480.0;

#[derive(Serialize, Deserialize, Clone, Copy, Debug)]
struct WindowBounds {
    width: u32,
    height: u32,
    x: Option<i32>,
    y: Option<i32>,
}

fn bounds_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("window.json"))
}

fn load_bounds(app: &tauri::AppHandle) -> Option<WindowBounds> {
    let path = bounds_path(app)?;
    let raw = fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_bounds(app: &tauri::AppHandle, bounds: &WindowBounds) {
    let Some(path) = bounds_path(app) else { return };
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(json) = serde_json::to_string(bounds) {
        let _ = fs::write(path, json);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .invoke_handler(tauri::generate_handler![])
        .setup(|app| {
            let window = app.get_webview_window("main").expect("main window");
            let scale = window.scale_factor().unwrap_or(1.0);
            let min_w = (MIN_WIDTH_LOGICAL * scale) as u32;
            let min_h = (MIN_HEIGHT_LOGICAL * scale) as u32;

            if let Some(b) = load_bounds(app.handle()) {
                if b.width >= min_w && b.height >= min_h {
                    let _ = window.set_size(PhysicalSize::new(b.width, b.height));
                    if let (Some(x), Some(y)) = (b.x, b.y) {
                        let _ = window.set_position(PhysicalPosition::new(x, y));
                    }
                }
            }

            let app_handle = app.handle().clone();
            let win = window.clone();
            window.on_window_event(move |event| {
                if !matches!(event, WindowEvent::Resized(_) | WindowEvent::Moved(_)) {
                    return;
                }
                if win.is_minimized().unwrap_or(false) {
                    return;
                }
                if win.is_fullscreen().unwrap_or(false) {
                    return;
                }
                let Ok(size) = win.outer_size() else { return };
                if size.width < min_w || size.height < min_h {
                    return;
                }
                let position = win.outer_position().ok();
                write_bounds(
                    &app_handle,
                    &WindowBounds {
                        width: size.width,
                        height: size.height,
                        x: position.map(|p| p.x),
                        y: position.map(|p| p.y),
                    },
                );
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
