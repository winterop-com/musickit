"""Shared frontend assets for the desktop SPA + `musickit ui`.

Historically this module also hosted a server-rendered `/web` browser
UI mounted inside `musickit serve`. That UI was removed in 0.20.4 —
the standalone `musickit ui` command now serves the same SPA against
any Subsonic server, so the embedded path was redundant.

What's left:

  - `static/` holds the canonical copies of the shared SPA assets
    (`app.css`, `_palette.css`, `visualizer.js`, `favicon.svg`). The
    Makefile target `desktop-sync-frontend` copies these into
    `desktop/frontend/` so Tauri / Electron bundle them; the build
    helper `scripts/copy_ui_static.py` mirrors the desktop frontend
    into `src/musickit/_ui_static/` so `musickit ui` can serve them
    from the installed wheel.

Keeping the directory under `musickit/web/static/` rather than
renaming it avoids churn in the desktop sync target and tests; treat
"web" as historical naming for "browser-facing assets" rather than
implying a co-hosted UI.
"""

from __future__ import annotations
