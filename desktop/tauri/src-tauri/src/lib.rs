//! MusicKit desktop entrypoint.
//!
//! Phase 1 MVP — minimal Tauri shell. Loads `src/index.html` (the URL
//! picker) into a single window. Picker is responsible for reading the
//! saved server URL from the store plugin and navigating the webview
//! to it; on subsequent launches the picker auto-redirects after a
//! brief delay so the app feels like it "remembers" the server.
//!
//! The Rust side stays as thin as possible: register plugins, run the
//! app. All UX lives in `src/`.

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            #[cfg(debug_assertions)]
            if let Some(window) = app.get_webview_window("main") {
                window.open_devtools();
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
