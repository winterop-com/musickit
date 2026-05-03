"""Media endpoints — `stream` / `download` (audio bytes) + `getCoverArt`."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from musickit.serve.app import error_envelope
from musickit.serve.covers import load_album_cover, resize
from musickit.serve.index import IndexCache
from musickit.serve.payloads import content_type

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _safe_path_under_root(cache: IndexCache, path_id: str) -> JSONResponse | None:
    """Defense: refuse IDs that resolve to paths outside the library root."""
    pair = cache.tracks_by_id.get(path_id)
    if pair is None:
        return JSONResponse(error_envelope(70, f"Song not found: {path_id}"))
    _, track = pair
    try:
        track.path.resolve().relative_to(cache.root.resolve())
    except ValueError:
        return JSONResponse(error_envelope(70, "track path escapes library root"))
    return None


@router.get("/stream")
@router.get("/stream.view")
async def stream(request: Request, id: str = Query(...)) -> Response:
    """Audio bytes for a track. Starlette's FileResponse handles HTTP Range natively."""
    cache = _get_cache(request)
    err = _safe_path_under_root(cache, id)
    if err is not None:
        return err
    pair = cache.tracks_by_id[id]
    _, track = pair
    return FileResponse(
        track.path,
        media_type=content_type(track),
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/download")
@router.get("/download.view")
async def download(request: Request, id: str = Query(...)) -> Response:
    """Same as `stream` for now — we never transcode, so there's no distinction."""
    return await stream(request, id=id)


@router.get("/getCoverArt")
@router.get("/getCoverArt.view")
async def get_cover_art(
    request: Request,
    id: str = Query(...),
    size: int | None = Query(default=None, ge=1, le=2000),
) -> Response:
    """Cover image for an album/song/artist ID. Optional `?size=N` resize via Pillow."""
    cache = _get_cache(request)
    if id.startswith("al_"):
        album = cache.albums_by_id.get(id)
    elif id.startswith("tr_"):
        pair = cache.tracks_by_id.get(id)
        album = pair[0] if pair is not None else None
    elif id.startswith("ar_"):
        # Use the first album of the artist (alphabetical) as the artist cover.
        albums = cache.artists_by_id.get(id)
        album = albums[0] if albums else None
    else:
        return JSONResponse(error_envelope(70, f"Unknown id format: {id}"))

    if album is None:
        return JSONResponse(error_envelope(70, f"Cover not found: {id}"))

    cover = load_album_cover(album)
    if cover is None:
        return JSONResponse(error_envelope(70, "no cover art for this album"))

    data, mime = cover
    if size is not None:
        try:
            data, mime = resize(data, max_size=size)
        except Exception:  # pragma: no cover — Pillow refused; fall back to original bytes
            pass
    return Response(content=data, media_type=mime)
