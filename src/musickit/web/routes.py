"""HTML routes for the browser UI.

Surface:
  - `GET  /`              -> redirect to `/web` (or `/login` if no session)
  - `GET  /login`         -> login form
  - `POST /login`         -> verify creds, set session, redirect to `/web`
  - `POST /logout`        -> clear session, redirect to `/login`
  - `GET  /web`           -> three-pane shell + initial artist list
  - `GET  /web/artist/{ar_id}` -> album list HTML fragment for one artist
  - `GET  /web/album/{al_id}`  -> track list HTML fragment for one album

Fragments are returned as plain HTML so the page's JS can do
`element.innerHTML = await fetch(...).text()` without parsing JSON.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from musickit import __version__, radio
from musickit.serve.auth import AuthError, verify
from musickit.web.session import SESSION_PW_KEY, SESSION_USER_KEY, new_csrf_token

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _is_authed(request: Request) -> bool:
    sess = request.session
    return bool(sess.get(SESSION_USER_KEY)) and bool(sess.get(SESSION_PW_KEY))


def _require_auth_or_redirect(request: Request) -> RedirectResponse | None:
    if _is_authed(request):
        return None
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Top-level + auth routes
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form(request: Request) -> Response:
    """Render the login form."""
    if _is_authed(request):
        return RedirectResponse(url="/web", status_code=303)
    csrf = new_csrf_token()
    request.session["csrf"] = csrf
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csrf": csrf, "error": None, "asset_version": __version__},
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(...),
) -> Response:
    """Verify creds against the existing Subsonic auth, then store in session."""
    expected_csrf = request.session.get("csrf")
    if not expected_csrf or csrf != expected_csrf:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf": new_csrf_token(),
                "error": "Session expired — please try again.",
                "asset_version": __version__,
            },
            status_code=400,
        )
    cfg = request.app.state.cfg
    try:
        verify(cfg, user=username, password=password, token=None, salt=None)
    except AuthError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf": new_csrf_token(),
                "error": "Wrong username or password.",
                "asset_version": __version__,
            },
            status_code=401,
        )
    request.session[SESSION_USER_KEY] = username
    request.session[SESSION_PW_KEY] = password
    request.session.pop("csrf", None)
    return RedirectResponse(url="/web", status_code=303)


@router.post("/logout", include_in_schema=False)
async def logout(request: Request) -> Response:
    """Clear the session + redirect to the login page."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Browser shell + fragments
# ---------------------------------------------------------------------------


@router.get("/web", response_class=HTMLResponse, include_in_schema=False)
async def web_shell(request: Request) -> Response:
    """Three-pane shell — Library stats + artists in left, albums + tracks in middle / right."""
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    cache = request.app.state.cache
    artists = sorted(
        ((ar_id, name) for ar_id, name in cache.artist_name_by_id.items()),
        key=lambda pair: pair[1].casefold(),
    )
    # Library stats — counts mirror the TUI's SidebarStats panel.
    folder_count = len({album.path.parent for album in cache.albums_by_id.values()})
    stats = {
        "tracks": cache.track_count,
        "albums": cache.album_count,
        "artists": cache.artist_count,
        "folders": folder_count,
    }
    return templates.TemplateResponse(
        request,
        "shell.html",
        {
            "artists": artists,
            "stats": stats,
            "user": request.session.get(SESSION_USER_KEY, ""),
            "version": __version__,
            # `asset_version` cache-busts /web-static/{app.css,app.js,...}
            # links so a `make` -> reload picks up CSS / JS changes
            # without forcing the user into Cmd+Shift+R every time.
            "asset_version": __version__,
        },
    )


@router.get("/web/radio", response_class=HTMLResponse, include_in_schema=False)
async def web_radio(request: Request) -> Response:
    """HTML fragment: internet radio station list backed by `radio.load_stations()`."""
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    stations = radio.load_stations()
    return templates.TemplateResponse(request, "radio.html", {"stations": stations})


@router.get("/web/radio-stream", include_in_schema=False)
async def web_radio_stream(request: Request, url: str) -> Response:
    """Same-origin proxy for a radio station's upstream stream.

    Why: the visualizer sets `audio.crossOrigin = "anonymous"` so Web
    Audio can read samples for the FFT. Once that's set, browsers issue
    a CORS preflight for any cross-origin `audio.src`, and most radio
    servers (Icecast / SHOUTcast) don't return CORS headers — playback
    fails silently. Routing the stream through the same origin
    sidesteps CORS entirely; the visualizer keeps working over the
    live stream.

    Security: the URL must match a station from `radio.load_stations()`.
    Without that gate this endpoint would be an open proxy.
    """
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    allowed = {s.url for s in radio.load_stations()}
    if url not in allowed:
        return Response("Unknown station", status_code=403)

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    upstream = await client.send(
        client.build_request("GET", url, headers={"User-Agent": f"musickit/{__version__}"}),
        stream=True,
        follow_redirects=True,
    )
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        return Response(f"Upstream {upstream.status_code}", status_code=502)

    async def streamer():  # type: ignore[no-untyped-def]
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        streamer(),
        media_type=upstream.headers.get("content-type", "audio/mpeg"),
    )


@router.get("/web/artist/{ar_id}", response_class=HTMLResponse, include_in_schema=False)
async def web_artist(request: Request, ar_id: str) -> Response:
    """HTML fragment: album list for one artist. Returned as a `<ul>` snippet."""
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    cache = request.app.state.cache
    albums = sorted(
        cache.artists_by_id.get(ar_id, []),
        key=lambda a: ((a.tag_year or "9999"), (a.tag_album or a.album_dir).casefold()),
    )
    items = []
    for album in albums:
        # Resolve the album's stable Subsonic id by reverse-lookup.
        al_id = next((aid for aid, a in cache.albums_by_id.items() if a is album), None)
        if al_id is None:
            continue
        items.append(
            {
                "id": al_id,
                "title": album.tag_album or album.album_dir,
                "year": album.tag_year or "",
                "track_count": album.track_count,
            }
        )
    return templates.TemplateResponse(request, "albums.html", {"albums": items})


@router.get("/web/album/{al_id}", response_class=HTMLResponse, include_in_schema=False)
async def web_album(request: Request, al_id: str) -> Response:
    """HTML fragment: track list for one album."""
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    cache = request.app.state.cache
    album = cache.albums_by_id.get(al_id)
    if album is None:
        return HTMLResponse("<p class='empty'>Album not found.</p>", status_code=404)
    tracks = []
    for track in album.tracks:
        tr_id = next(
            (tid for tid, (a, t) in cache.tracks_by_id.items() if a is album and t is track),
            None,
        )
        if tr_id is None:
            continue
        tracks.append(
            {
                "id": tr_id,
                "track_no": track.track_no or 0,
                "title": track.title or track.path.stem,
                "artist": track.artist or album.artist_dir,
                "duration": _fmt_mmss(track.duration_s or 0.0),
            }
        )
    return templates.TemplateResponse(
        request,
        "tracks.html",
        {
            "tracks": tracks,
            "album_id": al_id,
            "album_title": album.tag_album or album.album_dir,
            "album_artist": album.tag_album_artist or album.artist_dir,
            "album_year": album.tag_year or "",
        },
    )


@router.get("/web/search", response_class=HTMLResponse, include_in_schema=False)
async def web_search(request: Request, q: str = "") -> Response:
    """HTML fragment: combined artist + album + track search results.

    Uses the FTS5 index when available (sub-ms ranked); falls back to
    a casefolded substring scan when SQLite was built without FTS5.
    Caps each kind at 30 hits — the right pane isn't a paging UI.
    """
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    cache = request.app.state.cache
    query = q.strip()
    artists: list[dict[str, str]] = []
    albums: list[dict[str, str | int]] = []
    tracks: list[dict[str, str | int]] = []

    if query:
        from musickit.serve import search_index

        if cache.fts is not None:
            for ar_id in search_index.query(cache.fts, query, kind="artist", limit=30):
                name = cache.artist_name_by_id.get(ar_id)
                if name:
                    artists.append({"id": ar_id, "name": name})
            for al_id in search_index.query(cache.fts, query, kind="album", limit=30):
                album = cache.albums_by_id.get(al_id)
                if album is None:
                    continue
                albums.append(
                    {
                        "id": al_id,
                        "title": album.tag_album or album.album_dir,
                        "artist": album.tag_album_artist or album.artist_dir,
                        "year": album.tag_year or "",
                    }
                )
            for tr_id in search_index.query(cache.fts, query, kind="song", limit=30):
                pair = cache.tracks_by_id.get(tr_id)
                if pair is None:
                    continue
                album, track = pair
                track_album_id = next(
                    (aid for aid, a in cache.albums_by_id.items() if a is album),
                    "",
                )
                tracks.append(
                    {
                        "id": tr_id,
                        "album_id": track_album_id,
                        "title": track.title or track.path.stem,
                        "artist": track.artist or album.artist_dir,
                        "duration": _fmt_mmss(track.duration_s or 0.0),
                    }
                )
        else:
            # Fallback: casefolded substring across artist names + album titles
            # + track titles. Only fires when FTS5 is unavailable; mostly a
            # smoke for completeness.
            needle = query.casefold()
            for ar_id, name in cache.artist_name_by_id.items():
                if needle in name.casefold():
                    artists.append({"id": ar_id, "name": name})
            for al_id, album in cache.albums_by_id.items():
                title = album.tag_album or album.album_dir
                if needle in title.casefold():
                    albums.append(
                        {
                            "id": al_id,
                            "title": title,
                            "artist": album.tag_album_artist or album.artist_dir,
                            "year": album.tag_year or "",
                        }
                    )
            for tr_id, (album, track) in cache.tracks_by_id.items():
                title = track.title or track.path.stem
                if needle in title.casefold():
                    track_album_id = next(
                        (aid for aid, a in cache.albums_by_id.items() if a is album),
                        "",
                    )
                    tracks.append(
                        {
                            "id": tr_id,
                            "album_id": track_album_id,
                            "title": title,
                            "artist": track.artist or album.artist_dir,
                            "duration": _fmt_mmss(track.duration_s or 0.0),
                        }
                    )

    return templates.TemplateResponse(
        request,
        "search.html",
        {"query": query, "artists": artists[:30], "albums": albums[:30], "tracks": tracks[:30]},
    )


def _fmt_mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"
