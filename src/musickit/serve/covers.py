"""Album cover loading + optional resize for `/getCoverArt`.

Resolution order:
  1. Sidecar files in the album dir (`cover.jpg`, `folder.jpg`, `front.jpg`,
     `cover.png`). Preferred — convert pipeline writes these.
  2. Embedded picture in the first track's tags (mutagen via `read_source`).

Returned bytes are passed straight to the HTTP response unless `?size=` was
given, in which case Pillow resizes (capped to keep clients from asking
for a 5000px PNG that crushes RAM).
"""

from __future__ import annotations

import io

from musickit.library.models import LibraryAlbum
from musickit.metadata import read_source

_SIDECAR_NAMES = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.jpeg",
    "front.png",
)

_MAX_RESIZE = 1500  # cap user-requested cover size — saves memory on phones requesting silly sizes


def load_album_cover(album: LibraryAlbum) -> tuple[bytes, str] | None:
    """Return `(bytes, mime)` for the album's cover, or None if there isn't one."""
    sidecar = _find_sidecar(album)
    if sidecar is not None:
        return sidecar
    if not album.tracks:
        return None
    first = album.tracks[0]
    try:
        source = read_source(first.path, light=False)
    except Exception:  # pragma: no cover — best effort; broken file falls through
        return None
    if source.embedded_picture:
        mime = source.embedded_picture_mime or "image/jpeg"
        return source.embedded_picture, mime
    return None


def _find_sidecar(album: LibraryAlbum) -> tuple[bytes, str] | None:
    for name in _SIDECAR_NAMES:
        path = album.path / name
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return data, mime
    return None


def resize(data: bytes, *, max_size: int) -> tuple[bytes, str]:
    """Pillow `thumbnail` to fit `max_size` (capped at 1500). Re-encodes as JPEG (or PNG if alpha)."""
    from PIL import Image  # local import — keeps Pillow off the import path of pure-data modules

    target = max(1, min(_MAX_RESIZE, max_size))
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image.thumbnail((target, target))
        out = io.BytesIO()
        if image.mode == "RGBA":
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        rgb.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg"
