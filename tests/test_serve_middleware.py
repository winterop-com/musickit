"""Middleware stack — PostFormToQuery, SubsonicFormat, CORS.

Three middlewares run on every `/rest/*` request and silently shape
behaviour: form-body credentials get merged into the query string for
play:Sub, the response gets converted to XML when the client didn't
ask for JSON, and CORS headers are appended so cross-origin webviews
(MusicKit desktop apps) can read responses.

These have been load-bearing for months but had no direct test
coverage — this file fills that gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


# ---------------------------------------------------------------------------
# PostFormToQueryMiddleware
#
# play:Sub (and some older Subsonic clients) send credentials in a form-
# encoded POST body rather than a query string. Auth + endpoint code only
# reads from `request.query_params`, so without this middleware the auth
# would 401 every play:Sub request.
# ---------------------------------------------------------------------------


def test_post_form_body_credentials_authenticate(tmp_path: Path) -> None:
    """`POST /rest/ping` with form-encoded `u=&p=&f=json` authenticates fine."""
    response = _client(tmp_path).post(
        "/rest/ping",
        data={"u": "mort", "p": "secret", "f": "json"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subsonic-response"]["status"] == "ok"


def test_post_form_body_wrong_password_rejected(tmp_path: Path) -> None:
    """The merged form params still go through the auth dep — wrong password fails."""
    response = _client(tmp_path).post(
        "/rest/ping",
        data={"u": "mort", "p": "WRONG", "f": "json"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    body = response.json()
    assert body["subsonic-response"]["status"] == "failed"
    assert body["subsonic-response"]["error"]["code"] == 40


def test_post_form_body_merges_with_existing_query_string(tmp_path: Path) -> None:
    """Form-body params get appended to any pre-existing query string."""
    response = _client(tmp_path).post(
        "/rest/ping?f=json",
        data={"u": "mort", "p": "secret"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    # `f=json` came from the URL, `u/p` from the body — both must be honoured.
    assert response.headers["content-type"].startswith("application/json")


def test_post_with_non_form_content_type_passes_through(tmp_path: Path) -> None:
    """JSON-body POSTs aren't merged — only `application/x-www-form-urlencoded`."""
    # Send creds in the query string so auth passes, JSON body that the
    # endpoint would normally ignore.
    response = _client(tmp_path).post(
        "/rest/ping?u=mort&p=secret&f=json",
        json={"foo": "bar"},
    )
    assert response.status_code == 200


def test_get_unchanged_by_form_middleware(tmp_path: Path) -> None:
    """GET requests aren't touched by the form middleware."""
    response = _client(tmp_path).get(
        "/rest/ping",
        params={"u": "mort", "p": "secret", "f": "json"},
    )
    assert response.status_code == 200
    assert response.json()["subsonic-response"]["status"] == "ok"


# ---------------------------------------------------------------------------
# SubsonicFormatMiddleware
#
# Spec default is XML. Clients that don't pass `f=json` (Amperfy +
# older DSub) need an XML envelope or they fail to parse the response.
# ---------------------------------------------------------------------------


def test_no_f_param_returns_xml(tmp_path: Path) -> None:
    """Without `f=json`, the spec default is XML."""
    response = _client(tmp_path).get(
        "/rest/ping",
        params={"u": "mort", "p": "secret"},
    )
    assert response.status_code == 200
    ct = response.headers["content-type"]
    assert ct.startswith("application/xml") or ct.startswith("text/xml")
    body = response.text
    # Loose XML-shape check; we don't ship an XML parser into tests.
    assert "<subsonic-response" in body
    assert 'status="ok"' in body


def test_f_xml_returns_xml(tmp_path: Path) -> None:
    """`f=xml` is the spec-explicit default; behaves identical to no f."""
    response = _client(tmp_path).get(
        "/rest/ping",
        params={"u": "mort", "p": "secret", "f": "xml"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(("application/xml", "text/xml"))


def test_f_json_returns_json(tmp_path: Path) -> None:
    """`f=json` opts into JSON, bypassing the XML conversion."""
    response = _client(tmp_path).get(
        "/rest/ping",
        params={"u": "mort", "p": "secret", "f": "json"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["subsonic-response"]["status"] == "ok"


def test_non_rest_path_unaffected(tmp_path: Path) -> None:
    """Non-/rest/* responses pass through as-is, no XML conversion."""
    # Root landing page is HTML (since 0.20.6) — confirming the
    # Subsonic-format middleware doesn't touch non-/rest/* responses.
    response = _client(tmp_path).get("/", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "MusicKit" in body
    assert "/docs" in body


# ---------------------------------------------------------------------------
# CORSMiddleware
#
# Added in v0.12.1 so the MusicKit desktop wrappers (Tauri / Electron)
# could read /rest/* responses cross-origin. Auth is via `?u=&t=&s=`
# tokens, not origin, so `allow_origins=["*"]` is safe.
# ---------------------------------------------------------------------------


def test_cors_header_on_simple_response(tmp_path: Path) -> None:
    """Every /rest/* response carries Access-Control-Allow-Origin."""
    response = _client(tmp_path).get(
        "/rest/ping",
        params={"u": "mort", "p": "secret", "f": "json"},
        headers={"origin": "http://desktop.app"},
    )
    assert response.status_code == 200
    # `*` means any origin can read; matches our explicit
    # `allow_origins=["*"]` config.
    assert response.headers.get("access-control-allow-origin") == "*"


def test_cors_preflight_options(tmp_path: Path) -> None:
    """OPTIONS preflight succeeds with permissive Allow-* headers."""
    response = _client(tmp_path).options(
        "/rest/ping",
        headers={
            "origin": "http://desktop.app",
            "access-control-request-method": "GET",
            "access-control-request-headers": "x-custom",
        },
    )
    # Either 200 or 204 is acceptable per the CORS spec.
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "*"


@pytest.mark.parametrize(
    "endpoint",
    [
        "/rest/ping",
        "/rest/getLicense",
        "/rest/getMusicFolders",
    ],
)
def test_cors_header_on_multiple_endpoints(tmp_path: Path, endpoint: str) -> None:
    """CORS applies uniformly across the API surface, not just /ping."""
    response = _client(tmp_path).get(
        endpoint,
        params={"u": "mort", "p": "secret", "f": "json"},
        headers={"origin": "http://x.example"},
    )
    assert response.headers.get("access-control-allow-origin") == "*"
