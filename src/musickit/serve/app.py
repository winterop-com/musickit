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
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from musickit.serve.auth import AuthError, verify
from musickit.serve.config import ServeConfig
from musickit.serve.index import IndexCache
from musickit.serve.xml import to_xml

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


def create_app(*, root: Path, cfg: ServeConfig) -> FastAPI:
    """Build the FastAPI app for `root` with the given credentials."""
    app = FastAPI(
        title="musickit",
        description="Subsonic-compatible API server for a converted musickit library.",
        version=SERVER_VERSION,
        docs_url=None,  # the OpenAPI docs collide with `?u=&p=` — keep them off for now
        redoc_url=None,
    )
    app.state.root = root
    app.state.cfg = cfg
    app.state.cache = IndexCache(root)

    # Spec default is XML; clients opt into JSON via `?f=json`. Convert here
    # so endpoints stay simple (return dicts; the middleware emits the right
    # serialization). Binary responses (stream / cover) skip conversion via
    # the content-type check below.
    app.add_middleware(SubsonicFormatMiddleware)

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
    from musickit.serve.endpoints.media import router as media_router
    from musickit.serve.endpoints.scan import router as scan_router
    from musickit.serve.endpoints.search import router as search_router
    from musickit.serve.endpoints.system import router as system_router

    auth_dep = [Depends(require_auth)]
    app.include_router(system_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(browsing_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(scan_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(media_router, prefix="/rest", dependencies=auth_dep)
    app.include_router(search_router, prefix="/rest", dependencies=auth_dep)

    # Root probe: Amperfy and some other clients hit `GET /` before `/rest/ping`
    # to confirm the host is reachable. Without this they get a 404 and refuse
    # to log in. The response body is informational + harmless to expose pre-auth.
    @app.get("/")
    async def server_info() -> dict[str, Any]:
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "type": "subsonic-compatible",
            "api": "/rest/",
            "spec": "https://opensubsonic.netlify.app/docs/api-reference/",
        }

    return app


class _SubsonicAuthError(Exception):
    """Internal — translated to a Subsonic error 40 response by the handler."""


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
