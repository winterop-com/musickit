"""Library scan control — `startScan` + `getScanStatus`.

Subsonic clients show a "Refresh" button somewhere in the UI; it hits
`startScan`, then polls `getScanStatus` until `scanning=false`. We back
both with the shared `IndexCache`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from musickit.serve.app import envelope
from musickit.serve.index import IndexCache

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _scan_status_payload(cache: IndexCache) -> dict[str, object]:
    return {"scanning": cache.scan_in_progress, "count": cache.track_count}


@router.api_route("/startScan", methods=["GET", "POST"])
@router.api_route("/startScan.view", methods=["GET", "POST"])
async def start_scan(request: Request) -> dict:
    """Kick a background rescan. Returns immediately with the new status."""
    cache = _get_cache(request)
    cache.start_background_rescan()
    return envelope("scanStatus", _scan_status_payload(cache))


@router.api_route("/getScanStatus", methods=["GET", "POST"])
@router.api_route("/getScanStatus.view", methods=["GET", "POST"])
async def get_scan_status(request: Request) -> dict:
    """Poll the rescan state. `scanning=false` once `rebuild()` returns."""
    cache = _get_cache(request)
    return envelope("scanStatus", _scan_status_payload(cache))
