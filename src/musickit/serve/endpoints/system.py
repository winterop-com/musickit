"""System endpoints — `ping`, `getLicense`, `getMusicFolders`.

Three smallest endpoints in the spec. Their job in Phase 1 is to prove
the auth dependency, the response envelope, and routing all wire up.
"""

from __future__ import annotations

from fastapi import APIRouter

from musickit.serve.app import envelope

router = APIRouter()


@router.api_route("/ping", methods=["GET", "POST", "HEAD"])
@router.api_route("/ping.view", methods=["GET", "POST", "HEAD"])
async def ping() -> dict:
    """Auth check — clients ping before doing anything else."""
    return envelope()


@router.api_route("/getLicense", methods=["GET", "POST", "HEAD"])
@router.api_route("/getLicense.view", methods=["GET", "POST", "HEAD"])
async def get_license() -> dict:
    """Subsonic was paid software; clients still check this. Always return valid."""
    return envelope(
        "license",
        {
            "valid": True,
            "email": "self-hosted@musickit.local",
            "licenseExpires": "2099-12-31T00:00:00.000Z",
        },
    )


@router.api_route("/getMusicFolders", methods=["GET", "POST", "HEAD"])
@router.api_route("/getMusicFolders.view", methods=["GET", "POST", "HEAD"])
async def get_music_folders() -> dict:
    """One folder for the whole library — we don't multi-mount."""
    return envelope(
        "musicFolders",
        {"musicFolder": [{"id": 1, "name": "Library"}]},
    )
