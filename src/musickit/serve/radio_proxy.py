"""Same-origin proxy for radio station streams plus ICY metadata parsing.

Why: browser audio elements with `crossOrigin = "anonymous"` (set by
the visualizer so Web Audio can read FFT samples) trigger CORS for any
cross-origin `audio.src`. Most Icecast / SHOUTcast servers don't return
CORS headers, so a direct cross-origin radio fetch fails silently and
playback never starts. Routing the stream through musickit serve
sidesteps the issue because the server's CORS middleware advertises
`Access-Control-Allow-Origin: *` on every response.

This module is the renderer-neutral half of that solution: the upstream
proxy generator + ICY-title parser. Two routes call into it:

  - `/web/radio-stream` (session-auth'd, browser UI from `web/routes.py`)
  - `/rest/radioStream` (Subsonic-auth'd, desktop wrappers from
    `serve/endpoints/radio.py`)
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
from fastapi import Response
from fastapi.responses import StreamingResponse

from musickit import __version__, radio

_icy_titles: dict[str, str] = {}
_ICY_TITLE_RE = re.compile(rb"StreamTitle='([^']*)';")


def latest_icy_title(url: str) -> str:
    """Return the last-seen ICY StreamTitle for a station URL, or empty."""
    return _icy_titles.get(url, "")


async def proxy_station_stream(url: str) -> Response:
    """Proxy a single radio station's upstream stream with ICY-title parsing.

    Validates `url` against `radio.load_stations()` so this isn't an open
    proxy. If the upstream advertises `icy-metaint`, the response body is
    split into audio bytes (yielded to the client) and inline metadata
    frames (parsed for StreamTitle and stashed in `_icy_titles`).
    """
    allowed = {s.url for s in radio.load_stations()}
    if url not in allowed:
        return Response("Unknown station", status_code=403)

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
    upstream = await client.send(
        client.build_request(
            "GET",
            url,
            headers={"User-Agent": f"musickit/{__version__}", "Icy-MetaData": "1"},
        ),
        stream=True,
        follow_redirects=True,
    )
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        return Response(f"Upstream {upstream.status_code}", status_code=502)

    metaint_header = upstream.headers.get("icy-metaint")
    metaint = int(metaint_header) if metaint_header and metaint_header.isdigit() else 0

    async def streamer() -> AsyncIterator[bytes]:
        try:
            if metaint == 0:
                async for chunk in upstream.aiter_raw():
                    yield chunk
                return
            buf = bytearray()
            state = "audio"
            audio_left = metaint
            meta_len = 0
            async for chunk in upstream.aiter_raw():
                buf.extend(chunk)
                while True:
                    if state == "audio":
                        if not buf:
                            break
                        n = min(audio_left, len(buf))
                        yield bytes(buf[:n])
                        del buf[:n]
                        audio_left -= n
                        if audio_left == 0:
                            state = "meta_len"
                        continue
                    if state == "meta_len":
                        if not buf:
                            break
                        meta_len = buf[0] * 16
                        del buf[:1]
                        if meta_len == 0:
                            audio_left = metaint
                            state = "audio"
                        else:
                            state = "meta_body"
                        continue
                    if len(buf) < meta_len:
                        break
                    meta_bytes = bytes(buf[:meta_len])
                    del buf[:meta_len]
                    text = meta_bytes.rstrip(b"\x00")
                    match = _ICY_TITLE_RE.search(text)
                    if match:
                        title = match.group(1).decode("utf-8", errors="replace").strip()
                        if title:
                            _icy_titles[url] = title
                    audio_left = metaint
                    state = "audio"
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        streamer(),
        media_type=upstream.headers.get("content-type", "audio/mpeg"),
    )
