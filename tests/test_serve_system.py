"""System endpoints — `/ping`, `/getLicense`, `/getMusicFolders`. Auth + envelope shape."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def test_ping_returns_ok_envelope(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/ping", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    # OpenSubsonic clients sniff version/type from the envelope.
    assert "version" in inner


def test_ping_post_works(tmp_path: Path) -> None:
    """Subsonic spec requires both GET and POST; POST keeps creds out of access logs."""
    form: dict[str, str] = {"u": "mort", "p": "secret", "f": "json"}
    body = _client(tmp_path).post("/rest/ping", data=form).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_get_license_always_valid(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getLicense", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    assert inner["license"]["valid"] is True


def test_get_music_folders_returns_one_folder(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getMusicFolders", params=_params()).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "ok"
    folders = inner["musicFolders"]["musicFolder"]
    assert len(folders) == 1
    assert folders[0]["id"] == 1


def test_view_alias_routes(tmp_path: Path) -> None:
    """Real Subsonic clients send `/rest/ping.view` as an alias; we mirror that."""
    body = _client(tmp_path).get("/rest/ping.view", params=_params()).json()
    assert body["subsonic-response"]["status"] == "ok"


def test_ping_rejects_wrong_password(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/ping", params={"u": "mort", "p": "wrong", "f": "json"}).json()
    inner = body["subsonic-response"]
    assert inner["status"] == "failed"
    # Spec error 40 = wrong username or password.
    assert inner["error"]["code"] == 40
