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

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

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
    return templates.TemplateResponse(request, "login.html", {"csrf": csrf, "error": None})


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
            {"csrf": new_csrf_token(), "error": "Session expired — please try again."},
            status_code=400,
        )
    cfg = request.app.state.cfg
    try:
        verify(cfg, user=username, password=password, token=None, salt=None)
    except AuthError:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf": new_csrf_token(), "error": "Wrong username or password."},
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
    """Three-pane shell — artists in left pane, empty albums + tracks panes."""
    redirect = _require_auth_or_redirect(request)
    if redirect is not None:
        return redirect
    cache = request.app.state.cache
    artists = sorted(
        ((ar_id, name) for ar_id, name in cache.artist_name_by_id.items()),
        key=lambda pair: pair[1].casefold(),
    )
    return templates.TemplateResponse(
        request,
        "shell.html",
        {"artists": artists, "user": request.session.get(SESSION_USER_KEY, "")},
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
            "album_title": album.tag_album or album.album_dir,
            "album_artist": album.tag_album_artist or album.artist_dir,
        },
    )


def _fmt_mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"
