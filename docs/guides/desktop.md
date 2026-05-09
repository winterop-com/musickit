# Desktop

MusicKit ships two native desktop wrappers around a generic Subsonic
client UI: a Tauri build (Rust + native WebKit on macOS) and an Electron
build (bundled Chromium). Both are first-class — pick the one whose
trade-offs you prefer.

| Aspect            | Tauri          | Electron     |
|-------------------|----------------|--------------|
| Binary size       | ~15 MB         | ~120 MB      |
| Memory at idle    | ~80 MB         | ~250 MB      |
| Renderer          | OS WebView     | Chromium     |
| CSS / JS quirks   | WebKit-only    | Chrome-only  |
| Update path       | Self-contained | Self-contained |

## What it is

Both wrappers point the embedded webview at any Subsonic-compatible
server — your own `musickit serve`, [Navidrome], [Airsonic], whatever.
They're not just thin chrome around the web UI; the SPA inside
(`desktop/frontend/js/`) is a full client: salted-token auth, refresh-
restore via URL hashes, and a Web Audio FFT visualizer.

You log in once with **URL + Username + Password**; the app
computes the Subsonic salted token and re-uses it across browse / play
calls.

## Install

Right now: **build from source on your own machine.** We don't publish
desktop binaries to GitHub Releases, because shipping unsigned `.dmg` /
`.exe` files past Gatekeeper / SmartScreen is a worse user experience
than building locally. Code-signing setup (Apple Developer ID +
Authenticode) is on the [roadmap](../roadmap.md); once that lands, the
GHA matrix builds will come back.

```bash
git clone https://github.com/winterop-com/musickit.git
cd musickit
make desktop-tauri-build      # ~5 min on Apple Silicon — produces .app + .dmg
# OR
make desktop-electron-build   # ~3 min — produces .dmg
```

Both build outputs land under `desktop/{tauri,electron}/`; see
"Building from source" below.

## Connecting

1. Launch the app — you get a login screen.
2. Server URL: `http://<host>:4533` (or whatever your Subsonic-compatible
   server runs on). No `/rest` suffix.
3. Username + password: same credentials as `musickit serve`'s config.
4. The app connects, fetches the artist list, and remembers the
   credentials in OS-encrypted storage (Keychain on macOS, libsecret
   on Linux, Credential Manager on Windows).

## Refresh-restore

The app encodes the current artist / album / track in the URL hash:

```
http://localhost:1420/#a=1234&l=4567&t=8901
```

Reload the window (Cmd-R / F5) and you land back on the same track —
useful when iterating on `desktop/frontend/` during development.

## Building from source

Pick a wrapper, then run the matching make target. Both honor a
shared frontend at `desktop/frontend/`, so changes to the SPA propagate
to both.

```bash
make desktop-tauri-dev          # Tauri dev shell — hot reload of the SPA
make desktop-tauri-build        # Release Tauri bundle (.dmg + .app)
make desktop-electron-dev       # Electron dev shell
make desktop-electron-build     # Release Electron build
make build                      # Both, plus the Python wheel
```

The make targets sync `desktop/frontend/` from the canonical web UI
copies under `src/musickit/web/static/` before each build, so a single
edit there updates both wrappers.

## Trade-offs vs. the browser

The browser at `http://<host>:4533/web` does most of the same things —
why use the desktop apps? Three reasons:

1. **No tab.** A standalone window with the visualizer running in the
   background, no risk of accidentally closing it.
2. **Media keys.** macOS Now Playing + global play/pause keys (the
   browser version's keymap is hijacked by your active tab).
3. **Persistent credentials.** Encrypted at the OS layer, not browser
   localStorage.

If none of those matter, the browser is fine.

[Navidrome]: https://www.navidrome.org/
[Airsonic]: https://airsonic.github.io/
