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

# Placeholder cover served when an album has no embedded artwork. A 256x256
# SVG with a single ♪ glyph centred on the same `--border-soft` colour the
# web UI uses for its cover cells. Why a placeholder rather than 404:
# Subsonic clients vary widely in how they handle missing covers — Feishin
# (Electron) shows the browser's broken-image marker, play:Sub leaves an
# empty box, only Symfonium / Amperfy ship integrated fallbacks. Returning
# a real image with HTTP 200 means *every* client renders something
# sensible. Navidrome takes the same approach.
_COVER_PLACEHOLDER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
    b'<rect width="256" height="256" fill="#2a2b3a"/>'
    b'<text x="128" y="172" text-anchor="middle" font-family="sans-serif" '
    b'font-size="160" font-weight="600" fill="#565f89">\xe2\x99\xaa</text>'
    b"</svg>"
)
_COVER_PLACEHOLDER_MIME = "image/svg+xml"

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


def _transcode_response(path: Path, *, bitrate_kbps: int, time_offset_s: int = 0) -> StreamingResponse:
    """Pipe ffmpeg stdout into the HTTP response body as MP3.

    `time_offset_s` skips the first N seconds — used by the OpenSubsonic
    `transcodeOffset` extension so clients can seek into a transcoded
    stream without the server transcoding from 0 every time. Placed
    BEFORE `-i` so ffmpeg uses fast format-level seeking.
    """

    async def stream_iter() -> AsyncIterator[bytes]:
        args = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if time_offset_s > 0:
            args += ["-ss", str(time_offset_s)]
        args += [
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
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
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
@router.api_route("/stream.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def stream(
    request: Request,
    id: str = Query(...),
    format: str | None = Query(default=None, description="raw | mp3 (default: original)"),
    maxBitRate: int | None = Query(default=None, ge=0, le=512),
    timeOffset: int | None = Query(
        default=None,
        ge=0,
        description="OpenSubsonic transcodeOffset — start the transcode N seconds in.",
    ),
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
        # `timeOffset` is meaningful only on transcoded streams. Raw playback
        # has Range support so the client can seek without our help; ignoring
        # the param here matches the spec.
        return FileResponse(
            track.path,
            media_type=content_type(track),
            headers={"Accept-Ranges": "bytes"},
        )
    return _transcode_response(
        track.path,
        bitrate_kbps=bitrate,
        time_offset_s=timeOffset or 0,
    )


@router.api_route("/download", methods=["GET", "POST", "HEAD"])
@router.api_route("/download.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
@router.api_route("/getCoverArt.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
        # ID didn't resolve — treat as missing cover, not a hard error.
        # Returning the placeholder keeps the row rendering sensibly across
        # every Subsonic client.
        return Response(content=_COVER_PLACEHOLDER, media_type=_COVER_PLACEHOLDER_MIME)

    # Key on the URL `id` directly. Two different track IDs from the same
    # album yield two cache entries — fine. The point is to short-circuit
    # repeated requests for the same `id` (a mobile client paging through
    # albums fires the same `id` once per render), not to dedupe across IDs.
    cache_key = (id, size)
    cached = cache.cover_cache.get(cache_key)
    if cached is not None:
        data, mime = cached
        return Response(content=data, media_type=mime)

    cover = load_album_cover(album)
    if cover is None:
        # No embedded art on this album — serve the SVG placeholder so
        # clients that don't render their own fallback (Feishin, play:Sub,
        # the bundled web UI before v0.10.1) still get something sensible.
        # We DON'T cache the placeholder under `cache_key` because adding
        # cover art later should clear the miss without touching the cache.
        return Response(content=_COVER_PLACEHOLDER, media_type=_COVER_PLACEHOLDER_MIME)

    data, mime = cover
    if size is not None:
        try:
            data, mime = resize(data, max_size=size)
        except Exception:  # pragma: no cover — Pillow refused; fall back to original bytes
            pass
    cache.cover_cache.put(cache_key, data, mime)
    return Response(content=data, media_type=mime)
