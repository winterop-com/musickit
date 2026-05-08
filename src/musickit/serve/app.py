"""FastAPI app factory + Subsonic response envelope helpers.

Every Subsonic response is wrapped in `{"subsonic-response": {status, version, ...}}`.
We build the envelope here and return it from each endpoint. Errors are
shaped the same way with `status="failed"` + an error code/message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from musickit.serve.auth import AuthError, verify
from musickit.serve.config import ServeConfig
from musickit.serve.index import IndexCache
from musickit.serve.xml import to_xml
from musickit.web.session import RestQueryAuthFromSessionMiddleware, derive_session_secret

API_VERSION = "1.16.1"
SERVER_NAME = "musickit"
SERVER_VERSION = "0.1.0"


def envelope(payload_key: str | None = None, payload: Any = None) -> dict[str, Any]:
    """Wrap `payload` in the Subsonic ok-response envelope."""
    body: dict[str, Any] = {
        "status": "ok",
        "version": API_VERSION,
        "type": SERVER_NAME,
        "serverVersion": SERVER_VERSION,
        "openSubsonic": True,
    }
    if payload_key is not None:
        body[payload_key] = payload if payload is not None else {}
    return {"subsonic-response": body}


def error_envelope(code: int, message: str) -> dict[str, Any]:
    """Wrap an error in the Subsonic failed-response envelope."""
    return {
        "subsonic-response": {
            "status": "failed",
            "version": API_VERSION,
            "type": SERVER_NAME,
            "serverVersion": SERVER_VERSION,
            "error": {"code": code, "message": message},
        }
    }


def create_app(*, root: Path, cfg: ServeConfig, use_cache: bool = True, enable_web: bool = True) -> FastAPI:
    """Build the FastAPI app for `root` with the given credentials.

    `use_cache=False` disables the persistent `<root>/.musickit/index.db`
    and falls back to in-memory scan on every rebuild.

    `enable_web=False` skips mounting the browser UI (/login, /web/*,
    /web-static/*). The Subsonic `/rest/*` API stays fully available.
    Useful when you only want the Subsonic surface on a host (smaller
    attack area) or when running headless on a TV/embedded box where
    nobody will visit / in a browser.
    """
    app = FastAPI(
        title="musickit",
        description="Subsonic-compatible API server for a converted musickit library.",
        version=SERVER_VERSION,
        docs_url=None,  # the OpenAPI docs collide with `?u=&p=` — keep them off for now
        redoc_url=None,
    )
    app.state.root = root
    app.state.cfg = cfg
    app.state.cache = IndexCache(root, use_cache=use_cache)
    # Stars / favourites — separate file from the index DB (which is
    # fully derived and gets wiped on schema bumps). User data lives at
    # `<root>/.musickit/stars.toml`; survives `library index drop`.
    from musickit.serve.stars import StarStore

    app.state.stars = StarStore.for_root(root)

    # Scrobble forwarder — only spun up when `[scrobble.webhook]` or
    # `[scrobble.mqtt]` is in serve.toml. The dispatcher's `dispatch()`
    # is a no-op when both are unset, so keeping it on `app.state` even
    # in the disabled case keeps the endpoint code branchless.
    from musickit.serve.scrobble import ScrobbleDispatcher

    app.state.scrobble = ScrobbleDispatcher(cfg.scrobble)

    # Middleware order is REVERSE of registration — last add_middleware
    # is the outermost wrap. We need:
    #   request -> Session -> RestQueryAuthFromSession -> PostFormToQuery
    #            -> SubsonicFormat -> route
    # so that:
    #   - Session sets request.session before RestQueryAuth reads it.
    #   - RestQueryAuth injects ?u=&p= so the existing auth dep works for
    #     browser <audio src='/rest/stream?id=...'> calls.
    #   - PostForm merges form-body credentials so play:Sub works.
    #   - SubsonicFormat converts JSON responses to XML when needed.
    app.add_middleware(SubsonicFormatMiddleware)
    app.add_middleware(PostFormToQueryMiddleware)
    app.add_middleware(RestQueryAuthFromSessionMiddleware)
    # Sign cookies with a key derived from the password. Changing the
    # password invalidates every existing session — desirable. 30-day
    # sliding expiry is plenty for self-hosted use.
    app.add_middleware(
        SessionMiddleware,
        secret_key=derive_session_secret(cfg.password),
        session_cookie="musickit_session",
        max_age=30 * 24 * 60 * 60,
        same_site="lax",
        https_only=False,
    )

    async def require_auth(
        request: Request,
        u: str | None = Query(default=None),
        p: str | None = Query(default=None),
        t: str | None = Query(default=None),
        s: str | None = Query(default=None),
    ) -> None:
        """FastAPI dependency that enforces Subsonic auth on every endpoint."""
        del request
        try:
            verify(cfg, user=u, password=p, token=t, salt=s)
        except AuthError as exc:
            raise _SubsonicAuthError(str(exc)) from exc

    app.state.require_auth = require_auth

    @app.exception_handler(_SubsonicAuthError)
    async def auth_exception_handler(_request: Request, exc: _SubsonicAuthError) -> JSONResponse:
        return JSONResponse(error_envelope(40, str(exc)))

    # Mount endpoint groups. Imports happen lazily to keep the module graph
    # shallow and to avoid pulling FastAPI into pure-data modules.
    from musickit.serve.endpoints.browsing import router as browsing_router
    from musickit.serve.endpoints.extras import router as extras_router
    from musickit.serve.endpoints.lyrics import router as lyrics_router
    from musickit.serve.endpoints.media import router as media_router
    from musickit.serve.endpoints.radio import router as radio_router
    from musickit.serve.endpoints.scan import router as scan_router
    from musickit.serve.endpoints.search import router as search_router
    from musickit.serve.endpoints.stubs import router as stubs_router
    from musickit.serve.endpoints.system import router as system_router

    auth_dep = [Depends(require_auth)]
    app.include_router(system_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(browsing_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(scan_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(media_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(search_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(extras_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(lyrics_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(radio_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(stubs_router, prefix="/rest", dependencies=auth_dep)

    # Web UI — login + three-pane browse + audio player. Lives at /login,
    # /web, /web/artist/{id}, /web/album/{id}. Static assets served via
    # the StaticFiles mount below. Skipped entirely when `enable_web=False`.
    if enable_web:
        from musickit.web.routes import router as web_router

        app.include_router(web_router)
        web_static_dir = Path(__file__).resolve().parents[1] / "web" / "static"
        app.mount("/web-static", StaticFiles(directory=str(web_static_dir)), name="web-static")

    # Root probe — JSON for Subsonic clients (Amperfy hits / pre-login
    # to confirm the host is reachable), HTML redirect for browsers.
    # When the web UI is disabled, browsers also get the JSON body
    # (there's no /login to redirect to).
    @app.get("/")
    async def server_info(request: Request) -> Response:
        accept = request.headers.get("accept", "")
        if "text/html" in accept and enable_web:
            return RedirectResponse(url="/login", status_code=303)
        return JSONResponse(
            {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "type": "subsonic-compatible",
                "api": "/rest/",
                "spec": "https://opensubsonic.netlify.app/docs/api-reference/",
            }
        )

    return app


class _SubsonicAuthError(Exception):
    """Internal — translated to a Subsonic error 40 response by the handler."""


class PostFormToQueryMiddleware:
    """Merge POST form-body params into the query string for `/rest/*` requests.

    play:Sub (and some other older clients) send Subsonic credentials in
    an `application/x-www-form-urlencoded` POST body, not the query string.
    Our endpoints + auth dependency read everything from `request.query_params`,
    so we synthesise a merged query_string on the ASGI scope and replay the
    body for downstream consumers. After this middleware, the rest of the
    stack treats POST + form body identically to GET + query string.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith("/rest/")
        ):
            await self.app(scope, receive, send)
            return

        content_type = b""
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == b"content-type":
                content_type = header_value
                break
        if not content_type.startswith(b"application/x-www-form-urlencoded"):
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b"") or b"")
            more_body = message.get("more_body", False)
        body = b"".join(body_chunks)

        if body:
            existing_qs = scope.get("query_string", b"")
            merged_qs = existing_qs + b"&" + body if existing_qs else body
            scope = {**scope, "query_string": merged_qs}

        replayed = False

        async def replay_receive() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


class SubsonicFormatMiddleware(BaseHTTPMiddleware):
    """Convert JSON `/rest/*` responses to XML when `?f=json` is absent.

    Subsonic's spec default is XML; many clients (Amperfy, play:Sub,
    older DSub builds) don't pass `f=json` and silently fail to parse
    a JSON body. Our endpoints return dicts → FastAPI serializes them
    as JSON → this middleware re-serializes as XML when the client
    didn't explicitly ask for JSON. Binary responses (audio, cover
    images) are skipped via the content-type check.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:  # noqa: D102
        if not request.url.path.startswith("/rest/"):
            return await call_next(request)  # type: ignore[no-any-return]
        wants_json = request.query_params.get("f") == "json"
        if wants_json:
            return await call_next(request)  # type: ignore[no-any-return]

        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if not ct.startswith("application/json"):
            return response  # type: ignore[no-any-return]

        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
        try:
            data = json.loads(body)
            xml_body = to_xml(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Not a Subsonic envelope after all — pass through as-is.
            return Response(
                content=body,
                status_code=response.status_code,
                media_type="application/json",
            )
        return Response(
            content=xml_body,
            status_code=response.status_code,
            media_type="application/xml; charset=utf-8",
        )
