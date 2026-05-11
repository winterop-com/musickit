"""FastAPI app factory + Subsonic response envelope helpers.

Every Subsonic response is wrapped in `{"subsonic-response": {status, version, ...}}`.
We build the envelope here and return it from each endpoint. Errors are
shaped the same way with `status="failed"` + an error code/message.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from musickit import __version__
from musickit.serve.auth import AuthError, verify
from musickit.serve.config import ServeConfig
from musickit.serve.index import IndexCache
from musickit.serve.xml import to_xml

_access_log = structlog.stdlib.get_logger("musickit.serve.access")

API_VERSION = "1.16.1"
SERVER_NAME = "musickit"
# Pulled from the installed package metadata via `importlib.metadata`
# (set in `musickit/__init__.py`). Avoids the stale "0.1.0" string that
# used to live here and forget to track `pyproject.toml`'s version bumps.
SERVER_VERSION = __version__


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


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """App lifespan: bump anyio's thread limiter, yield, no shutdown work.

    The default 40-thread cap is too small under chatty Subsonic clients.
    Starlette's FileResponse reads each file in a worker thread, and
    play:Sub on iOS is known to open 30-50 parallel `/rest/stream`
    connections for a single track (aggressive prefetch). With 40 threads
    those alone exhaust the pool and every other request — web UI / TUI /
    other clients — blocks. 256 threads sleeping on disk I/O are cheap;
    the OS handles them comfortably. We bump in `lifespan` rather than at
    `create_app` time because the limiter is per-event-loop and only
    accessible once the loop is running.
    """
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = 256
    yield


def create_app(*, root: Path, cfg: ServeConfig, use_cache: bool = True) -> FastAPI:
    """Build the FastAPI app for `root` with the given credentials.

    `use_cache=False` disables the persistent `<root>/.musickit/index.db`
    and falls back to in-memory scan on every rebuild.

    The server exposes only the Subsonic `/rest/*` surface; the embedded
    `/web` browser UI was removed in 0.20.4 in favour of the
    standalone `musickit ui` command, which serves the same SPA against
    any Subsonic server without needing one running in-process.
    """
    app = FastAPI(
        title="musickit",
        description="Subsonic-compatible API server for a converted musickit library.",
        version=SERVER_VERSION,
        docs_url=None,  # the OpenAPI docs collide with `?u=&p=` — keep them off for now
        redoc_url=None,
        lifespan=_lifespan,
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
    #   request -> CORS -> PostFormToQuery -> SubsonicFormat -> route
    # so that:
    #   - CORS handles cross-origin preflights + appends headers to
    #     responses. Required for `musickit ui` and the MusicKit
    #     desktop wrappers (Tauri / Electron) — they load from a
    #     `tauri://` / `file://` / `http://localhost:1888` origin and
    #     fetch `http://server/rest/*` cross-origin; without CORS
    #     headers the browser blocks the response from JS access even
    #     though the server returned 200.
    #   - PostForm merges form-body credentials so play:Sub works.
    #   - SubsonicFormat converts JSON responses to XML when needed.
    #
    # The session + session-to-query middleware pair used to live here
    # for the embedded `/web` UI (cookies → `<audio src=/rest/stream>`
    # auth). Both went away with `/web` itself in 0.20.4 — Subsonic
    # `/rest/*` only ever needs salted-token auth from query params.
    app.add_middleware(SubsonicFormatMiddleware)
    app.add_middleware(PostFormToQueryMiddleware)
    # Access log — one structured line per HTTP request, with the
    # canonical Apache combined fields (client, user, request line,
    # status, bytes, referer, user_agent) plus a duration_ms for
    # easy slow-endpoint spotting. Outputs through the same
    # structlog handler `configure_logging()` installed, so JSON
    # mode produces one record per request that log shippers can
    # pivot on without regex parsing.
    app.add_middleware(AccessLogMiddleware)
    # CORS — outermost. We allow any origin because the Subsonic auth
    # token (`?u=&t=&s=`) is the security boundary, not the request
    # origin. This lets `musickit ui` (and the desktop wrappers) talk
    # to a remote musickit serve from their own origin without the
    # webview blocking the response. Other Subsonic clients (Symfonium
    # / Amperfy / play:Sub) aren't browsers and don't care about CORS
    # either way; this is purely additive.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
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

    # Root probe — JSON only. Browsers visiting `/` get the same
    # introspection payload Subsonic clients hit on pre-login: server
    # name, version, and a pointer to the Subsonic API surface. The
    # browser UI is served by `musickit ui` (a separate static-file
    # server); this server is pure Subsonic now.
    @app.get("/")
    async def server_info(request: Request) -> Response:
        del request
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


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured log line per HTTP request.

    Carries the Apache combined log fields (client IP, authenticated
    user — Subsonic `?u=` query param — method, path, HTTP version,
    status code, response bytes, Referer, User-Agent) plus a
    `duration_ms` for slow-endpoint spotting. Routes through the
    structlog handler `configure_logging()` installed, so JSON mode
    produces one machine-parseable record per request.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        # `call_next` is `Callable[[Request], Awaitable[Response]]`;
        # left as `Any` because the BaseHTTPMiddleware stub types it
        # vaguely. Cast the return so mypy sees the concrete `Response`.
        response: Response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        client = request.client.host if request.client else "-"
        # Subsonic auth puts the username in `?u=`; surface it where an
        # Apache log would carry `%u`. Falls back to "-" for any
        # request that hasn't authenticated yet (root probe, OPTIONS).
        user = request.query_params.get("u") or "-"
        http_version = request.scope.get("http_version", "1.1")
        bytes_sent = response.headers.get("content-length", "-")
        _access_log.info(
            "request",
            client=client,
            user=user,
            method=request.method,
            path=request.url.path,
            http_version=http_version,
            status=response.status_code,
            bytes=bytes_sent,
            referer=request.headers.get("referer", "-"),
            user_agent=request.headers.get("user-agent", "-"),
            duration_ms=elapsed_ms,
        )
        return response


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
