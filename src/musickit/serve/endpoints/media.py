"""Media endpoints — `stream` / `download` (audio bytes) + `getCoverArt`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from musickit.library.models import LibraryTrack
from musickit.serve.app import error_envelope
from musickit.serve.covers import load_album_cover, resize
from musickit.serve.index import IndexCache
from musickit.serve.payloads import content_type

router = APIRouter()

# Subsonic spec: transcoding default target is MP3. 192k is a reasonable
# quality/size compromise; clients can lower it via `maxBitRate`.
_DEFAULT_TRANSCODE_BITRATE_KBPS = 192


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


def _resolve_transcode(
    track: LibraryTrack,
    fmt: str | None,
    max_bitrate: int | None,
) -> tuple[bool, int]:
    """Decide whether to transcode and at what bitrate (Kbps).

    `format=raw` always wins (no transcode). `format=mp3` with a non-MP3
    source triggers a transcode. `maxBitRate>0` alone also triggers a
    transcode to MP3 — the spec says clients use that to cap delivered
    bitrate. The common case (no params) returns False so the
    FileResponse Range path stays free.
    """
    fmt_l = (fmt or "").lower()
    if fmt_l == "raw":
        return False, 0
    if fmt_l == "mp3" and track.path.suffix.lower() != ".mp3":
        target = max_bitrate if (max_bitrate and max_bitrate > 0) else _DEFAULT_TRANSCODE_BITRATE_KBPS
        return True, target
    if max_bitrate and max_bitrate > 0:
        return True, max_bitrate
    return False, 0


def _transcode_response(path: Path, *, bitrate_kbps: int) -> StreamingResponse:
    """Pipe ffmpeg stdout into the HTTP response body as MP3."""

    async def stream_iter() -> AsyncIterator[bytes]:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",  # drop any embedded picture stream
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            "-f",
            "mp3",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            # Ensure ffmpeg exits if the client disconnects mid-stream — without
            # this the subprocess would linger and pin a CPU core.
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:  # pragma: no cover — already exited
                    pass
                await process.wait()

    return StreamingResponse(stream_iter(), media_type="audio/mpeg")


@router.api_route("/stream", methods=["GET", "POST", "HEAD"])
@router.api_route("/stream.view", methods=["GET", "POST", "HEAD"])
async def stream(
    request: Request,
    id: str = Query(...),
    format: str | None = Query(default=None, description="raw | mp3 (default: original)"),
    maxBitRate: int | None = Query(default=None, ge=0, le=512),
) -> Response:
    """Audio bytes for a track. Transcodes to MP3 when the client asks; otherwise raw with Range."""
    cache = _get_cache(request)
    err = _safe_path_under_root(cache, id)
    if err is not None:
        return err
    pair = cache.tracks_by_id[id]
    _, track = pair

    transcode, bitrate = _resolve_transcode(track, format, maxBitRate)
    if not transcode:
        return FileResponse(
            track.path,
            media_type=content_type(track),
            headers={"Accept-Ranges": "bytes"},
        )
    return _transcode_response(track.path, bitrate_kbps=bitrate)


@router.api_route("/download", methods=["GET", "POST", "HEAD"])
@router.api_route("/download.view", methods=["GET", "POST", "HEAD"])
async def download(request: Request, id: str = Query(...)) -> Response:
    """Always raw bytes — `download` skips transcoding by spec."""
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


@router.api_route("/getCoverArt", methods=["GET", "POST", "HEAD"])
@router.api_route("/getCoverArt.view", methods=["GET", "POST", "HEAD"])
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
