"""PyAV container open + ICY/general metadata extraction helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import av

if TYPE_CHECKING:
    from av.container.input import InputContainer


def open_container(source: Path | str) -> tuple[InputContainer, Any]:
    """Open `source` (local path or URL) and return `(container, audio_stream)`.

    Caller owns the container. For URLs, `icy=1` opt-in is enabled so
    Icecast/Shoutcast metadata (station name + StreamTitle) shows up in
    `container.metadata`.
    """
    target = str(source)
    is_url = target.startswith(("http://", "https://"))
    options: dict[str, str] = {"icy": "1"} if is_url else {}
    container = av.open(target, options=options) if options else av.open(target)
    audio_streams = [s for s in container.streams if s.type == "audio"]
    if not audio_streams:
        container.close()
        raise ValueError(f"no audio stream in {source}")
    return container, audio_streams[0]


def get_metadata_value(container: InputContainer, key: str) -> str | None:
    """Fetch a single ICY/general metadata value from a container.

    Checks both the container-level `metadata` (most ICY headers) and the
    first audio stream's metadata (PyAV exposes some fields on the stream
    side). Returns None if missing/blank.
    """
    for source in (container.metadata, *(s.metadata for s in container.streams)):
        if not source:
            continue
        value = source.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return None
