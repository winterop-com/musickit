"""Browser UI for `musickit serve` — three-pane library browser + audio player.

Mounted at `/` and `/web/*` of the same FastAPI app that serves the
Subsonic API. Reuses every `/rest/*` endpoint for data and audio
streaming; this module only adds:

  - `/login` form that takes user+password once and sets a signed
    session cookie. The existing Subsonic clients (Symfonium, Amperfy,
    play:Sub) keep using `?u=&p=` query auth — they don't see cookies.
  - `/web` three-pane shell (artists / albums / tracks).
  - `/web/artist/{id}` and `/web/album/{id}` HTML fragments that the
    browser swaps into the right pane. No SPA framework — just `fetch`
    + `innerHTML`.
  - Static CSS + JS, hand-written to keep the runtime free of
    third-party JS deps.
  - A query-rewrite middleware: when a `/rest/*` request arrives with a
    valid session cookie but no `?u=&p=`, the cookie's stored creds get
    appended to the query string. That makes `<audio src='/rest/stream?id=...'>`
    work without leaking the password into every HTML page.
"""

from __future__ import annotations

from musickit.web.routes import router

__all__ = ["router"]
