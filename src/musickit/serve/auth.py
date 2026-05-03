"""Subsonic auth — plain `?p=` and salted-token `?t=md5(password+salt)&s=`.

Both forms are part of the v1.13.0+ spec; modern clients use token, but
older or simpler ones (curl, mpDris) use plain. We accept either, and an
`enc:<hex>` plain-password variant some clients send.
"""

from __future__ import annotations

import hashlib

from musickit.serve.config import ServeConfig


class AuthError(Exception):
    """Raised when auth fails — caller maps to Subsonic error 40."""


def verify(
    cfg: ServeConfig,
    *,
    user: str | None,
    password: str | None,
    token: str | None,
    salt: str | None,
) -> None:
    """Validate Subsonic credentials. Raises `AuthError` on any failure.

    Subsonic clients send EITHER `p=<password>` (plain or `enc:<hex>`) OR
    `t=<md5(password+salt)>&s=<salt>`. We accept both.
    """
    if not user:
        raise AuthError("missing username")
    if user != cfg.username:
        raise AuthError("wrong username or password")
    if token is not None and salt is not None:
        expected = hashlib.md5((cfg.password + salt).encode("utf-8")).hexdigest()  # noqa: S324
        # MD5 here is mandated by the Subsonic spec — not our choice and not used
        # for anything secret-bearing (it's a challenge response over a salt).
        if token.lower() != expected.lower():
            raise AuthError("wrong username or password")
        return
    if password is None:
        raise AuthError("missing password or token")
    plain = _decode_password(password)
    if plain != cfg.password:
        raise AuthError("wrong username or password")


def _decode_password(value: str) -> str:
    """Decode the `enc:<hex>` form some clients use; pass plain through."""
    if value.startswith("enc:"):
        try:
            return bytes.fromhex(value[4:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value
    return value
