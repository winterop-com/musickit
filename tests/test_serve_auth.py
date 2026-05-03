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
    response = _client(tmp_path).get("/rest/ping")
    body = response.json()
    inner = body["subsonic-response"]
    assert inner["status"] == "failed"
    assert inner["error"]["code"] == 40


def test_ping_plain_password_round_trips(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "secret"})
    body = response.json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["version"] == "1.16.1"
    assert inner["openSubsonic"] is True


def test_ping_enc_password_round_trips(tmp_path: Path) -> None:
    """`enc:<hex>` is what some clients send to avoid logging plain passwords."""
    enc = "enc:" + b"secret".hex()
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": enc})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_ping_token_auth_round_trips(tmp_path: Path) -> None:
    salt = "rocksalt"
    token = hashlib.md5(("secret" + salt).encode()).hexdigest()  # noqa: S324
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "t": token, "s": salt})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_ping_wrong_password_returns_error_40(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "nope"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 40


def test_ping_wrong_token_returns_error_40(tmp_path: Path) -> None:
    salt = "rocksalt"
    bad_token = hashlib.md5(("wrong" + salt).encode()).hexdigest()  # noqa: S324
    response = _client(tmp_path).get("/rest/ping", params={"u": "mort", "t": bad_token, "s": salt})
    assert response.json()["subsonic-response"]["status"] == "failed"


def test_ping_view_alias_works(tmp_path: Path) -> None:
    """Older clients hit `/rest/ping.view`; newer ones hit `/rest/ping`. Both must work."""
    response = _client(tmp_path).get("/rest/ping.view", params={"u": "mort", "p": "secret"})
    assert response.json()["subsonic-response"]["status"] == "ok"


def test_get_license_returns_valid(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/getLicense", params={"u": "mort", "p": "secret"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "ok"
    assert body["license"]["valid"] is True


def test_get_music_folders_returns_one_library(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/rest/getMusicFolders", params={"u": "mort", "p": "secret"})
    body = response.json()["subsonic-response"]
    assert body["status"] == "ok"
    folders = body["musicFolders"]["musicFolder"]
    assert len(folders) == 1
    assert folders[0]["name"] == "Library"
