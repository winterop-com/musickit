"""Subsonic-compatible HTTP server for the converted musickit library.

`musickit serve [DIR]` launches a FastAPI app that exposes the library
via the Subsonic API (v1.16.1). Any Subsonic client (Symfonium, play:Sub,
Feishin, DSub, etc.) can browse, search, and stream from it.
"""

from __future__ import annotations

from musickit.serve.app import create_app
from musickit.serve.config import ServeConfig, resolve_credentials

__all__ = ["ServeConfig", "create_app", "resolve_credentials"]
