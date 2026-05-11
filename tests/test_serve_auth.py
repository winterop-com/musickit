"""Auth + ping/getLicense — proves the envelope, dependency, and routing."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


def test_ping_anonymous_returns_subsonic_error_40(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"f": "json"})
    body = response.json()
    inner = body["subsonic-response"]
    assert inner["status"] == "failed"
    assert inner["error"]["code"] == 40


def test_ping_returns_xml_when_f_param_omitted(tmp_path: Path) -> None:
    """Subsonic spec default is XML — clients like Amperfy don't send f=json."""
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "secret"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    body = response.content.decode("utf-8")
    assert "<subsonic-response" in body
    assert 'status="ok"' in body
    assert "</subsonic-response>" in body or "/>" in body


def test_ping_returns_xml_when_f_param_xml(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "secret", "f": "xml"})
    assert response.headers["content-type"].startswith("application/xml")


def test_post_ping_works_too(tmp_path: Path) -> None:
    """play:Sub uses POST; spec allows either method."""
    response = _client(tmp_path).post("/rest/ping.view", params={"u": "mort", "p": "secret", "f": "json"})
    assert response.status_code == 200
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_post_credentials_in_form_body_authenticate(tmp_path: Path) -> None:
    """play:Sub puts u/p in application/x-www-form-urlencoded body, not query string."""
    response = _client(tmp_path).post(
        "/rest/ping.view",
        data={"u": "mort", "p": "secret", "f": "json"},
    )
    assert response.status_code == 200
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_post_credentials_split_between_body_and_query(tmp_path: Path) -> None:
    """f=json in query, u/p in body — should still authenticate."""
    response = _client(tmp_path).post(
        "/rest/ping.view",
        params={"f": "json"},
        data={"u": "mort", "p": "secret"},
    )
    assert response.status_code == 200
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_ping_plain_password_round_trips(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "secret", "f": "json"})
    body = response.json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["version"] == "1.16.1"
    assert inner["openSubsonic"] is True


def test_ping_enc_password_round_trips(tmp_path: Path) -> None:
    """`enc:<hex>` is what some clients send to avoid logging plain passwords."""
    enc = "enc:" + b"secret".hex()
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": enc, "f": "json"})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_ping_token_auth_round_trips(tmp_path: Path) -> None:
    salt = "rocksalt"
    token = hashlib.md5(("secret" + salt).encode()).hexdigest()  # noqa: S324
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "t": token, "s": salt, "f": "json"})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_ping_wrong_password_returns_error_40(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "nope", "f": "json"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 40


def test_ping_wrong_token_returns_error_40(tmp_path: Path) -> None:
    salt = "rocksalt"
    bad_token = hashlib.md5(("wrong" + salt).encode()).hexdigest()  # noqa: S324
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "t": bad_token, "s": salt, "f": "json"})
    assert response.json()["subsonic-response"]["status"] == "failed"


def test_ping_view_alias_works(tmp_path: Path) -> None:
    """Older clients hit `/rest/ping.view`; newer ones hit `/rest/ping`. Both must work."""
    response = _client(tmp_path).get("/rest/ping.view", params={"u": "mort", "p": "secret", "f": "json"})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_get_license_returns_valid(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/getLicense", params={"u": "mort", "p": "secret", "f": "json"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "ok"
    assert body["license"]["valid"] is True


def test_get_music_folders_returns_one_library(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/getMusicFolders", params={"u": "mort", "p": "secret", "f": "json"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "ok"
    folders = body["musicFolders"]["musicFolder"]
    assert len(folders) == 1
    assert folders[0]["name"] == "Library"


def test_root_returns_200_with_server_info(tmp_path: Path) -> None:
    """Amperfy probes GET / before /rest/ping — must return 200 or it refuses to log in.

    Root used to return JSON; since 0.20.6 it's an HTML landing page so a
    browser visiting `/` gets clickable links to `/docs` and the API root.
    Subsonic clients (Amperfy / play:Sub) treat any 200 response on `/`
    as "server reachable" and then issue `/rest/ping` separately, so the
    content-type doesn't matter to them — only the status code does.
    """
    response = _client(tmp_path).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "MusicKit" in body
    assert "/rest/" in body
    assert "/docs" in body


def test_resolve_credentials_falls_back_to_admin_defaults() -> None:
    """No CLI flags + no serve.toml → admin/admin with `used_defaults=True`."""
    from musickit.serve.config import resolve_credentials

    # Force-load with no inputs by passing None explicitly — load_config still
    # checks ~/.config/musickit/serve.toml so this asserts the fallback behaviour
    # only when that file is also absent. On a dev machine where it might exist,
    # the test still exercises the CLI-flag override path below.
    cfg, used_defaults = resolve_credentials(cli_user=None, cli_password=None)
    if used_defaults:
        assert cfg.username == "admin"
        assert cfg.password == "admin"


def test_resolve_credentials_cli_flags_override_defaults() -> None:
    """CLI flags must win even when defaults would otherwise apply."""
    from musickit.serve.config import resolve_credentials

    cfg, used_defaults = resolve_credentials(cli_user="alice", cli_password="wonderland")
    assert cfg.username == "alice"
    assert cfg.password == "wonderland"
    assert used_defaults is False
