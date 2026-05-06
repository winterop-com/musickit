"""SubsonicClient._get malformed-response defenses (no real network)."""

from __future__ import annotations

import httpx
import pytest

from musickit.tui.subsonic_client import SubsonicClient, SubsonicError


def _client(handler: object) -> SubsonicClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.Client(transport=transport)
    return SubsonicClient("http://server", "u", password="p", http=http)


def test_top_level_array_raises() -> None:
    """A server that returns `[]` instead of `{...}` triggers SubsonicError, not TypeError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with pytest.raises(SubsonicError):
        _client(handler).ping()


def test_missing_subsonic_response_key_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": "wrong"})

    with pytest.raises(SubsonicError):
        _client(handler).ping()


def test_status_failed_envelope_raises_with_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"subsonic-response": {"status": "failed", "error": {"code": 40, "message": "wrong password"}}},
        )

    with pytest.raises(SubsonicError) as exc_info:
        _client(handler).ping()
    assert "40" in str(exc_info.value)


def test_5xx_raises_subsonicerror() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SubsonicError):
        _client(handler).ping()


def test_non_json_body_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(SubsonicError):
        _client(handler).ping()


def test_ok_envelope_parses() -> None:
    """Sanity: well-formed responses don't trigger any of the defensive paths."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"subsonic-response": {"status": "ok", "version": "1.16.1"}},
        )

    # No exception means the parse path is correct.
    _client(handler).ping()
