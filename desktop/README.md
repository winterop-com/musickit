# MusicKit Desktop

Generic Subsonic clients for the desktop. Two parallel implementations
under `tauri/` and `electron/` both load the same SPA from
`desktop/frontend/`. The SPA talks to **any spec-compliant Subsonic
server** (musickit serve, Navidrome, Airsonic, Gonic, …) via the
`/rest/*` JSON API; the password never crosses the wire after login
because we use the spec's salted-token auth (`token = md5(password +
salt)`).

## Layout

```
desktop/
├── frontend/                 ← shared SPA (HTML/CSS/JS, host-agnostic)
│   ├── index.html               entry — boots into login or shell
│   ├── shell.css                desktop-specific styles (login + tweaks)
│   ├── _app.css              → ../../src/musickit/web/static/app.css   (symlink)
│   ├── _palette.css          → ../../src/musickit/web/static/_palette.css
│   ├── favicon.svg           → ../../src/musickit/web/static/favicon.svg
│   └── js/
│       ├── app.js               state machine: login OR shell
│       ├── api.js               Subsonic API client (token auth, fetch wrapper)
│       ├── store.js             host-agnostic persistence (Tauri / Electron)
│       ├── shell.js             main browser (artists / albums / tracks / now-playing)
│       └── md5.js               pure-JS MD5 for salted-token computation
├── tauri/                    Rust backend
│   └── src-tauri/
└── electron/                 Node main + preload bridge
    └── src/
```

The login page accepts **URL + Username + Password**. On submit:

1. Generates a fresh salt + computes `token = md5(password + salt)`
2. Calls `<URL>/rest/ping?u=&t=&s=` to verify the server speaks
   Subsonic and the credentials work
3. Persists `{host, user, token, salt}` via the host store
4. Mounts the main shell

The raw password is never persisted; only the token is. Future
launches load the session and skip the login form.

## Run

```bash
make desktop-tauri              # alias for desktop-tauri-dev
make desktop-tauri-dev          # cargo tauri dev
make desktop-tauri-build        # release .app

make desktop-electron           # alias for desktop-electron-dev
make desktop-electron-dev       # electron .  (npm install on first run)
make desktop-electron-build     # release .dmg
```

You'll need a Subsonic-compatible server running somewhere — the
login form asks for its URL. For local testing:

```bash
musickit serve /path/to/library     # in another terminal
make desktop-tauri-dev
```

Then in the login form: `http://localhost:4533` + `admin` / `admin`.

## Tauri vs Electron

| | Tauri | Electron |
|---|---|---|
| Installed size | 10-20 MB | 150-200 MB |
| RAM (idle) | 50-100 MB | 200-400 MB |
| Webview engine | OS native (WebKit on macOS) | Bundled Chromium |
| Backend lang | Rust | Node |
| Linux audio quirks | WebKitGTK has known AAC gaps | Chromium uniform |
| Maturity | Newer ecosystem | Larger / older |

Both share the SPA so the client UX is identical. The Tauri-vs-Electron
comparison is about platform integration (system Now Playing widget,
auto-update, signing, Linux codec consistency).

## Phase status (v0.12.0)

**Phase A — Login + auth: done.** Server URL + credentials login,
salted-token persisted, sign-out wipes session.

**Phase B — Browse: done.** Three-pane shell. Artists from
`getArtists`, albums from `getArtist`, tracks from `getAlbum`. Cover
art via `getCoverArt`.

**Phase C — Playback: done.** Track click plays via `/rest/stream`;
auto-advance through the visible album; play/pause/next/prev buttons
+ space/n/p keybinds; Now Playing card with cover + metadata.

**Phase D — Polish: TODO** for v0.12.1+. Search, lyrics panel,
visualizer, command palette, repeat/shuffle, marquee titles.

**Phase E — Internet radio: TODO.** `getInternetRadioStations` is
already returned by musickit serve; click-to-play directly via
`<audio src="<station-url>">` should "just work".

**Phase F — Distribution: TODO.** Apple Developer ID + notarization
in the Tauri Action; Linux/Windows matrix builds.

## Move-out triggers

This stays in the monorepo until one of these fires:
1. First signed release ready to share
2. Desktop CI matrix lands and bloats the main CI
3. External contributor on the desktop side
4. README balance tipping (desktop dwarfing the rest)

Then `git subtree split --prefix=desktop -b desktop-only` extracts.
