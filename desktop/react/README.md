# MusicKit Design v2 (Claude Designer prototype)

In-progress redesign of the MusicKit desktop UI. The source files in
`src/` are the raw output of Claude Designer — sibling JSX modules
that share state via `window.MK_*` globals. `index.html` loads them
in dependency order via Babel-standalone so the prototype runs with
no build step (same runtime model as the Claude Designer preview).

This folder is **isolated from the production frontend** at
`desktop/frontend/`. Iterations on the design can land here without
touching the working app.

## Layout

```
desktop/react/
├── index.html          ← entry — boots React UMD + Babel + script tags
├── main.jsx            ← renders <window.MK_App /> into #root
├── musickit.css        ← all styles, theme tokens, layout variants
├── favicon.svg
└── src/
    ├── data.jsx           mock library data → window.MK_DATA
    ├── covers.jsx         procedural album-cover generator
    ├── visualizer.jsx     Canvas FFT (bars / mirror / radial / ambient)
    ├── views.jsx          LoginView, StarBtn, ConnectionBanner, ...
    ├── overlays.jsx       Shortcuts panel, command palette, search dropdown, lyrics overlay
    ├── tweaks-panel.jsx   Designer-time tweak controls (layout / accent / viz / density / ...)
    ├── chrome.jsx         TopBar, Sidebar, NowPlaying, MainArea, FullscreenViz, ...
    └── app.jsx            <App /> — wires state + actions, registers window.MK_App
```

## Run

```bash
make desktop-react-tauri-dev          # Tauri wrapper (separate app bundle)
make desktop-react-electron-dev       # Electron wrapper (separate app bundle)

# Or as a plain webpage:
cd desktop/react && python3 -m http.server 1900
open http://127.0.0.1:1900/
```

The prototype uses mock data (`src/data.jsx`) — no Subsonic server
required. Wiring to the real `/rest/*` API comes after the design
direction is locked.

## Why no build step

The Claude Designer artifact uses sibling globals on `window` rather
than ES module imports. Loading via `<script type="text/babel">` in
the same order as the design preview means every new iteration can
be dropped in with `cp -r`. When the design stabilises we can swap
to Vite + TypeScript without touching the JSX source.

## What this is NOT

- Not connected to a real server yet.
- Not the production UI — for that, use `make desktop-tauri-dev` or
  `make desktop-electron-dev`, which load `desktop/frontend/`.
- Not shipped to PyPI — only the production wrappers are.
