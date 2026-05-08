# MusicKit Desktop

Native desktop wrappers around the MusicKit web UI. Two parallel
implementations under `tauri/` and `electron/` so we can compare bundle
size, launch time, memory footprint, and audio-codec consistency
side-by-side.

## What this is (and isn't)

This is **a desktop wrapper for `musickit serve`** — it loads the
web UI the server already ships (`/web`) into a chromeless window. It
is NOT a generic Subsonic client today; most other Subsonic servers
(Navidrome, Airsonic, etc.) don't expose a `/web` endpoint with our
shape, so the desktop app currently only works against `musickit
serve` instances.

Becoming a true generic Subsonic client (i.e. embedding the UI inside
the desktop app and talking to any Subsonic-compatible server via the
`/rest/*` API only) is a future direction — not in this slice.

## Layout

```
desktop/
├── frontend/                 ← shared picker UI (HTML/CSS/JS)
│   ├── index.html               server-URL input page
│   ├── picker.css               @imports _palette.css
│   ├── picker.js                host-agnostic (Tauri OR Electron)
│   ├── _palette.css           → symlink to ../../src/musickit/web/static/_palette.css
│   └── favicon.svg            → symlink to ../../src/musickit/web/static/favicon.svg
├── tauri/
│   └── src-tauri/               Rust backend
└── electron/
    └── src/                     Node main + preload bridge
```

The picker is the only UI either wrapper renders by itself. After the
user enters a URL the webview navigates to `<URL>/web` and the server
delivers everything from there — login form, three-pane player,
visualizer, radio, lyrics. Cookies set by the server's login form
persist in the webview's cookie jar, so subsequent launches skip the
form and land on `/web` directly.

## Tauri vs Electron

Both consume the same `desktop/frontend/` so the picker UX is
identical. Differences kick in once the user is connected:

|  | Tauri | Electron |
|---|---|---|
| Installed size | 10-20 MB | 150-200 MB |
| RAM (idle) | 50-100 MB | 200-400 MB |
| Webview engine | OS native (WebKit on macOS) | Bundled Chromium |
| Backend lang | Rust | Node |
| Linux audio quirks | WebKitGTK has known AAC gaps | Chromium uniform |
| Maturity | Newer ecosystem | Larger / older |

Ship target is Tauri; Electron is here for empirical comparison and as
a fallback if Tauri's macOS WebKit hits a wall (e.g. unexpected `<audio>`
behaviour with our radio proxy or visualizer).

## Run

```bash
make desktop-tauri-dev          # opens window, hot-reloads on frontend/ changes
make desktop-tauri-build        # release .app under tauri/src-tauri/target/release/bundle/

make desktop-electron-dev       # opens window via electron .
make desktop-electron-build     # release .dmg under electron/dist/
```

First run downloads + compiles deps:
- Tauri: ~2-5 min for the Rust crates
- Electron: ~1-2 min for `npm install`

You'll need a `musickit serve` instance running somewhere — the picker
asks for its URL. For local testing:

```bash
musickit serve /path/to/library     # in another terminal
make desktop-tauri-dev              # then http://localhost:4533 in the picker
```

## Shared assets

The picker shares two files with the web UI via symlinks:

- `frontend/_palette.css → src/musickit/web/static/_palette.css` —
  the colour palette (Tokyo Night `night`). Editing it in either
  place updates both the browser and the desktop pickers.
- `frontend/favicon.svg → src/musickit/web/static/favicon.svg` —
  the `♪`-on-grey icon. Used by the web UI as `<link rel="icon">` and
  by the Tauri build as the source for its derived `.icns` / `.ico`.

Symlinks committed to git work fine on macOS + Linux; Windows requires
admin perms to create them, which is why the Tauri icon files are
checked in as derivatives (generated via `cargo tauri icon
icons/icon.png`) rather than symlinked from the web/static SVG.

## Phase status

**Phase 1 — done in this PR:**
- Tauri + Electron scaffolds compile / launch
- Server URL picker with persistent saved-servers list (Tauri's
  `tauri-plugin-store` and Electron's `electron-store` both expose the
  same `.get / .set / .save` surface to the picker)
- Window state (position, size) persisted across launches
- Webview navigates to `<URL>/web` on connect

**Phase 2 — next:** MediaSession metadata for the macOS Now Playing
widget, global media keys (Bluetooth headphone play/pause, etc.),
mDNS LAN discovery so the picker can pre-populate from
`musickit serve` advertisements.

**Phase 3 — distribution:** Apple Developer ID + notarization wired
into Tauri Action; auto-updater pointed at GitHub Releases. Apple
Developer ID is $99/yr; without it the `.app` opens with a scary
warning.

**Phase 4 — Linux + Windows matrix builds.**

## Move-out triggers

This stays in the monorepo until one of these fires:
1. First signed release ready to share
2. Desktop CI matrix lands and bloats the main CI
3. External contributor on the desktop side
4. README balance tipping (desktop dwarfing the rest)

Then `git subtree split --prefix=desktop -b desktop-only` produces a
clean history for the new repo.
