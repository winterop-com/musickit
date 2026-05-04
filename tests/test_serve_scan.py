"""startScan + getScanStatus — kicks a background rescan, polls state."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from musickit.serve import ServeConfig, create_app


def _client(tmp_path: Path) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    return TestClient(create_app(root=tmp_path, cfg=cfg))


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def test_get_scan_status_idle_by_default(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/rest/getScanStatus", params=_params()).json()["subsonic-response"]
    assert body["status"] == "ok"
    assert body["scanStatus"]["scanning"] is False
    assert body["scanStatus"]["count"] == 0


def test_start_scan_returns_status_envelope(tmp_path: Path) -> None:
    body = _client(tmp_path).post("/rest/startScan", params=_params()).json()["subsonic-response"]
    assert body["status"] == "ok"
    assert "scanning" in body["scanStatus"]


def test_start_scan_immediately_reports_scanning_true(tmp_path: Path) -> None:
    """Race regression: scan_in_progress must be set BEFORE the bg thread runs.

    Otherwise a fast client polling getScanStatus right after startScan
    could see scanning=false and stop polling before the rescan even
    started.
    """
    body = _client(tmp_path).post("/rest/startScan", params=_params()).json()["subsonic-response"]
    assert body["scanStatus"]["scanning"] is True


def test_start_scan_walks_real_directory(tmp_path: Path) -> None:
    """End-to-end: dump a fake album on disk, hit startScan, poll until done."""
    # Build a tiny "album" — empty m4a is fine, library.scan reads tags
    # via mutagen which tolerates a 0-byte file (just yields nothing).
    album = tmp_path / "TestArtist" / "TestAlbum"
    album.mkdir(parents=True)
    (album / "01 - Track.m4a").write_bytes(b"")

    client = _client(tmp_path)
    client.post("/rest/startScan", params=_params())
    # Poll up to 5s for the background scan to finish — it should complete
    # in milliseconds for an empty file but `scan` walks the disk.
    for _ in range(50):
        status = client.get("/rest/getScanStatus", params=_params()).json()["subsonic-response"]["scanStatus"]
        if not status["scanning"]:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("background scan never completed within 5s")
    assert status["scanning"] is False
