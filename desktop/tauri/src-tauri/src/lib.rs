//! MusicKit desktop entrypoint.
//!
//! The Rust side stays minimal — it registers plugins and runs the
//! Tauri app. Window size, position, and the entire UI are owned by
//! `tauri.conf.json` + `desktop/frontend/`.
//!
//! What we deliberately do NOT do:
//!
//!   - Auto-open DevTools. The user can press Cmd+Option+I (macOS)
//!     or right-click → Inspect Element when they want it. Auto-opening
//!     is jarring on every launch and the panel obscures the small
//!     login window.
//!
//!   - Use `tauri-plugin-window-state` (yet). Its v2 release on
//!     macOS has been intermittently saving sub-min sizes for us
//!     (window restored to 1780x106 after a small resize), so the
//!     window always opens at the explicit 1440x900 configured in
//!     `tauri.conf.json`. We'll re-add the plugin once we trust the
//!     saved-state behaviour.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .invoke_handler(tauri::generate_handler![])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
