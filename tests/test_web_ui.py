"""Web UI: login flow + session cookie + browse fragments + auth gating."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.ids import album_id, artist_id, track_id


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    track = LibraryTrack(
        path=tmp_path / "ABBA" / "Arrival" / "01.flac",
        title="Dancing Queen",
        artist="ABBA",
        album="Arrival",
        track_no=1,
        duration_s=230.0,
    )
    album = LibraryAlbum(
        path=tmp_path / "ABBA" / "Arrival",
        artist_dir="ABBA",
        album_dir="Arrival",
        tag_album="Arrival",
        tag_year="1976",
        tag_album_artist="ABBA",
        track_count=1,
        tracks=[track],
    )
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    return TestClient(app)


def _login(client: TestClient, *, username: str = "mort", password: str = "secret") -> None:
    """Helper: hit /login, post creds (with the CSRF token), follow no redirects."""
    form_page = client.get("/login")
    csrf = _extract_csrf(form_page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _extract_csrf(html: str) -> str:
    """Pull `value="..."` from the hidden CSRF input."""
    import re

    match = re.search(r'name="csrf"\s+value="([^"]+)"', html)
    assert match, "CSRF token not found in login form"
    return match.group(1)


# ---------------------------------------------------------------------------
# Anonymous access
# ---------------------------------------------------------------------------


def test_root_redirects_to_login_for_browser(tmp_path: Path) -> None:
    """`/` with `Accept: text/html` redirects to `/login`."""
    client = _client(tmp_path)
    response = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_returns_json_for_subsonic_clients(tmp_path: Path) -> None:
    """`/` with non-HTML Accept (Amperfy probe) still returns the JSON body."""
    client = _client(tmp_path)
    response = client.get("/", headers={"Accept": "application/json"})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "subsonic-compatible"
    assert body["api"] == "/rest/"


def test_login_form_renders(tmp_path: Path) -> None:
    """GET /login returns the form with a fresh CSRF token."""
    client = _client(tmp_path)
    response = client.get("/login")
    assert response.status_code == 200
    assert 'name="csrf"' in response.text
    assert 'name="username"' in response.text
    assert 'name="password"' in response.text


def test_web_redirects_to_login_when_anonymous(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/web", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_artist_fragment_redirects_to_login_when_anonymous(tmp_path: Path) -> None:
    """Fragment endpoints also gate behind the session cookie."""
    client = _client(tmp_path)
    response = client.get("/web/artist/foo", follow_redirects=False)
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Login form behaviour
# ---------------------------------------------------------------------------


def test_login_with_wrong_password_shows_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    form = client.get("/login")
    csrf = _extract_csrf(form.text)
    response = client.post(
        "/login",
        data={"username": "mort", "password": "WRONG", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Wrong username or password" in response.text


def test_login_with_bad_csrf_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.get("/login")
    response = client.post(
        "/login",
        data={"username": "mort", "password": "secret", "csrf": "not-the-real-token"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Session expired" in response.text


def test_successful_login_sets_session_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    # The session cookie is now in the client jar; subsequent /web hit succeeds.
    response = client.get("/web", follow_redirects=False)
    assert response.status_code == 200
    # The TUI-aligned shell uses panel-titled sections — "Library" + "Browse".
    assert "Library" in response.text
    assert "Browse" in response.text
    assert "ABBA" in response.text


# ---------------------------------------------------------------------------
# Authenticated browsing
# ---------------------------------------------------------------------------


def test_shell_renders_tui_alike_chrome(tmp_path: Path) -> None:
    """The shell carries the TUI-style chrome: Library stats, KeyBar, version brand."""
    client = _client(tmp_path)
    _login(client)
    text = client.get("/web", follow_redirects=False).text
    # Library stats panel + counts.
    assert "Library" in text
    assert "Tracks" in text and "Albums" in text and "Artists" in text and "Folders" in text
    # KeyBar at the bottom.
    assert 'class="keybar"' in text
    assert "·Play" in text
    # Centered topbar with versioned brand.
    assert 'class="brand"' in text
    assert 'class="version"' in text


def test_artist_fragment_returns_album_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    ar_id = artist_id("ABBA")
    response = client.get(f"/web/artist/{ar_id}")
    assert response.status_code == 200
    assert "Arrival" in response.text
    assert 'data-action="load-album"' in response.text


def test_album_fragment_returns_track_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    cache = client.app.state.cache  # type: ignore[attr-defined]
    al_id = next(iter(cache.albums_by_id))
    response = client.get(f"/web/album/{al_id}")
    assert response.status_code == 200
    assert "Dancing Queen" in response.text
    assert 'data-action="play-track"' in response.text
    # data-album-id is what the JS uses to fetch the now-playing cover.
    assert f'data-album-id="{al_id}"' in response.text


def test_album_fragment_links_cover_thumbnail(tmp_path: Path) -> None:
    """Album rows include an <img> pointing at /rest/getCoverArt for the album."""
    client = _client(tmp_path)
    _login(client)
    ar_id = artist_id("ABBA")
    response = client.get(f"/web/artist/{ar_id}")
    assert response.status_code == 200
    assert 'class="album-thumb"' in response.text
    assert "/rest/getCoverArt?id=" in response.text


def test_album_fragment_unknown_id_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/album/al_does_not_exist")
    assert response.status_code == 404
    assert "not found" in response.text.lower()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_returns_combined_fragment(tmp_path: Path) -> None:
    """`/web/search?q=` returns a fragment with artist + album + track sections."""
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/search?q=ABBA")
    assert response.status_code == 200
    assert "Search" in response.text
    # ABBA is an artist + album_artist + part of the track artist field; at
    # minimum the artist section should fire.
    assert "ABBA" in response.text


def test_search_empty_query_returns_no_results_section(tmp_path: Path) -> None:
    """An empty `q=` doesn't error — returns the shell with `No matches.`"""
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/search?q=")
    assert response.status_code == 200
    assert "No matches" in response.text


def test_search_finds_track_by_title(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    response = client.get("/web/search?q=Dancing")
    assert response.status_code == 200
    assert "Dancing Queen" in response.text


def test_search_requires_login(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/web/search?q=ABBA", follow_redirects=False)
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Audio streaming via session cookie (no ?u=&p= in the URL)
# ---------------------------------------------------------------------------


def test_stream_works_with_session_cookie(tmp_path: Path) -> None:
    """Browser sends `<audio src='/rest/stream?id=...'>` with no query auth.
    The session middleware injects u/p from the cookie before the auth dep.
    """
    # Real audio file so /rest/stream actually returns bytes.
    album_dir = tmp_path / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    track_path = album_dir / "01.m4a"
    track_path.write_bytes(b"fake-audio-bytes" * 100)
    track = LibraryTrack(path=track_path, title="T", track_no=1)
    album = LibraryAlbum(
        path=album_dir,
        artist_dir="Artist",
        album_dir="Album",
        tag_album="Album",
        track_count=1,
        tracks=[track],
    )
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    client = TestClient(app)

    _login(client)
    tr_id = track_id(track)
    # No `u`/`p` in the query — session cookie alone authorises.
    response = client.get(f"/rest/stream?id={tr_id}&f=raw")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/")


def test_stream_without_cookie_or_query_auth_rejected(tmp_path: Path) -> None:
    """No cookie + no `?u=&p=` → auth error 40."""
    client = _client(tmp_path)
    cache = client.app.state.cache  # type: ignore[attr-defined]
    tr_id = next(iter(cache.tracks_by_id))
    # `f=json` so the auth error envelope comes back as JSON (default is XML).
    response = client.get(f"/rest/stream?id={tr_id}&f=json")
    body = response.json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 40


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_clears_session(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _login(client)
    assert client.get("/web", follow_redirects=False).status_code == 200

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # /web is now redirecting again.
    response = client.get("/web", follow_redirects=False)
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Subsonic clients still work via query auth (cookie is irrelevant)
# ---------------------------------------------------------------------------


def test_subsonic_query_auth_unaffected_by_session_middleware(tmp_path: Path) -> None:
    """An existing Subsonic client sends ?u=&p= and never touches /login."""
    client = _client(tmp_path)
    response = client.get("/rest/ping", params={"u": "mort", "p": "secret", "f": "json"})
    assert response.status_code == 200
    assert response.json()["subsonic-response"]["status"] == "ok"
    # And the cookie jar is empty — no session was ever created.
    assert "musickit_session" not in client.cookies


# Silence "unused" warnings on imports kept for parity with other test files.
_ = album_id
