"""Session cookie auth — sign-on at `/login`, cookie carries (u,p) for `/rest/*`.

Uses Starlette's `SessionMiddleware` for the signed cookie. A separate
ASGI middleware rewrites `/rest/*` requests' query strings so an
`<audio src='/rest/stream?id=...'>` element sent from the browser still
passes the existing `verify(cfg, user=u, password=p)` auth dep — the
cookie's stored u/p get spliced into the query string before the
auth dep runs.

Existing Subsonic clients are unaffected: they always send `?u=&p=`
explicitly, and the rewrite is a no-op when those params are already
present.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode

from starlette.types import ASGIApp, Receive, Scope, Send

# Session keys.
SESSION_USER_KEY = "musickit_user"
SESSION_PW_KEY = "musickit_password"  # noqa: S105 — session storage key, not a credential


def derive_session_secret(cfg_password: str) -> str:
    """Derive a stable session-signing key from the configured password.

    Avoids a separate secret config knob: the password is already
    user-provided, and signing tokens against it means changing the
    password also invalidates every existing session (a desirable
    property). Hash + 32-hex chars so the key isn't the password itself
    in plaintext.
    """
    return hashlib.sha256(cfg_password.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    """Random 16-hex token for the login form.

    Submitted + verified by the POST handler so a phished login link to
    a third-party site can't post creds against this server's `/login`
    cross-origin.
    """
    return secrets.token_hex(8)


class RestQueryAuthFromSessionMiddleware:
    """Inject session-stored `u` / `p` into `/rest/*` query strings.

    Lets `<audio src='/rest/stream?id=tr_xyz'>` work for browser-mode
    requests. No-op when:
      - The path isn't `/rest/*`
      - There's no session cookie
      - The query already includes `u` (the existing Subsonic-client path)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not _is_rest_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        query_string: bytes = scope.get("query_string", b"") or b""
        existing = parse_qs(query_string.decode("utf-8"))
        if "u" in existing or "t" in existing:
            # Subsonic-client request — leave as-is.
            await self.app(scope, receive, send)
            return
        session = _session_from_scope(scope)
        if session is None:
            await self.app(scope, receive, send)
            return
        u = session.get(SESSION_USER_KEY)
        p = session.get(SESSION_PW_KEY)
        if not isinstance(u, str) or not isinstance(p, str) or not u or not p:
            await self.app(scope, receive, send)
            return
        injected = urlencode({"u": u, "p": p}).encode("utf-8")
        new_query = injected if not query_string else query_string + b"&" + injected
        scope = {**scope, "query_string": new_query}
        await self.app(scope, receive, send)


def _is_rest_path(path: str) -> bool:
    return path.startswith("/rest/")


def _session_from_scope(scope: Scope) -> dict[str, Any] | None:
    """Read Starlette's session dict off the scope (set by SessionMiddleware)."""
    session = scope.get("session")
    if isinstance(session, dict):
        return session
    return None
