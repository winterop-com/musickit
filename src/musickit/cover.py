"""Locate, normalize, and embed album cover art."""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from musickit.metadata import SourceTrack

_COVER_STEMS: tuple[str, ...] = ("cover", "folder", "front", "albumart")
_COVER_EXTS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")
# Token-level cover keyword match: catches scene-style filenames like
# `000_va_-_absolute_music_47_(swedish_edition)-2cd-2004-(front)-dqm.jpg` and
# `absolute music 45 front.jpg`. Negative lookarounds prevent false positives
# like `frontiers` or `coverage`. `_BACK_KEYWORD_RE` filters out matching
# `back` covers so we don't pick a back cover over a front cover.
_COVER_KEYWORD_RE = re.compile(r"(?<![a-z])(?:cover|folder|front|albumart)(?![a-z])", re.IGNORECASE)
_BACK_KEYWORD_RE = re.compile(r"(?<![a-z])back(?![a-z])", re.IGNORECASE)
DEFAULT_MAX_EDGE = 1000  # 1000×1000 JPEG is plenty for Music.app cover-flow + Finder previews.
_JPEG_QUALITY = 90


class CoverSource(str, Enum):
    """Where the cover came from. Used for reporting + tie-breaking under --enrich."""

    EMBEDDED = "embedded"
    FOLDER = "folder"
    ONLINE = "online"


class CoverCandidate(BaseModel):
    """A candidate cover image, before normalization."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: CoverSource
    data: bytes
    mime: str | None = None
    width: int = 0
    height: int = 0
    label: str = ""

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def size_bytes(self) -> int:
        return len(self.data)


class Cover(BaseModel):
    """A normalized album cover ready to embed into every track of an album."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: bytes
    mime: str  # "image/jpeg" or "image/png"
    width: int
    height: int
    source: CoverSource
    label: str


def collect_candidates(album_dir: Path, tracks: list[SourceTrack]) -> list[CoverCandidate]:
    """Gather every plausible cover candidate for an album (offline only)."""
    candidates: list[CoverCandidate] = []

    # Collect every distinct embedded picture across the album. Most albums
    # carry the same cover on every track, but mixed rips occasionally have
    # one track with corrected/larger artwork — let the picker compare them
    # all and pick the highest-area image. Drop unparseable bytes here so
    # they never reach `normalize()`.
    seen_embedded: set[bytes] = set()
    for track in tracks:
        if not track.embedded_picture:
            continue
        if track.embedded_picture in seen_embedded:
            continue
        seen_embedded.add(track.embedded_picture)
        width, height = _measure(track.embedded_picture)
        if width == 0 or height == 0:
            continue
        candidates.append(
            CoverCandidate(
                source=CoverSource.EMBEDDED,
                data=track.embedded_picture,
                mime=track.embedded_picture_mime,
                width=width,
                height=height,
                label=f"embedded in {track.path.name}",
            )
        )

    # Search the album anchor AND every disc subfolder for folder images.
    # For bare-leading multi-disc layouts the anchor IS the wrapper; for
    # shared-prefix layouts (`Album (CD1)`) the anchor is the first disc
    # folder, so the other disc subfolders need scanning too — and the
    # parent wrapper might carry a top-level `folder.jpg` shared across
    # both discs (anchor.parent).
    seen_dirs: set[Path] = {album_dir}
    search_dirs: list[Path] = [album_dir]
    if _looks_like_disc_anchor(album_dir):
        # Anchor is `<wrapper>/<Album (CD1)>` — also scan `<wrapper>` for the
        # shared cover that lives at the wrapper level.
        parent = album_dir.parent
        if parent not in seen_dirs and parent != album_dir:
            seen_dirs.add(parent)
            search_dirs.append(parent)
    for track in tracks:
        if track.path.parent not in seen_dirs:
            seen_dirs.add(track.path.parent)
            search_dirs.append(track.path.parent)

    seen_image_paths: set[Path] = set()
    for folder_dir in search_dirs:
        for path in _find_folder_images(folder_dir):
            if path in seen_image_paths:
                continue
            seen_image_paths.add(path)
            try:
                data = path.read_bytes()
            except OSError:
                continue
            width, height = _measure(data)
            if width == 0 or height == 0:
                # Pillow couldn't decode — image is corrupt or not actually
                # an image despite the extension. Skip it so it can't be
                # picked, normalised, and crash the album later.
                continue
            candidates.append(
                CoverCandidate(
                    source=CoverSource.FOLDER,
                    data=data,
                    mime=_guess_mime(path.suffix),
                    width=width,
                    height=height,
                    label=path.name,
                )
            )

    return candidates


def pick_best(candidates: Iterable[CoverCandidate]) -> CoverCandidate | None:
    """Pick the highest-quality candidate.

    "Quality" = pixel area first, then file size, then source order
    (online > folder > embedded). The source-order tiebreaker matters under
    `--enrich` so that an online provider returning the same dimensions as
    a 600×600 scanned folder.jpg still wins.
    """
    source_order = {CoverSource.ONLINE: 2, CoverSource.FOLDER: 1, CoverSource.EMBEDDED: 0}
    best: CoverCandidate | None = None
    for candidate in candidates:
        if best is None:
            best = candidate
            continue
        if candidate.pixels > best.pixels:
            best = candidate
        elif candidate.pixels == best.pixels and candidate.size_bytes > best.size_bytes:
            best = candidate
        elif (
            candidate.pixels == best.pixels
            and candidate.size_bytes == best.size_bytes
            and source_order[candidate.source] > source_order[best.source]
        ):
            best = candidate
    return best


def normalize(candidate: CoverCandidate, *, max_edge: int = DEFAULT_MAX_EDGE) -> Cover:
    """Decode + recompress the chosen candidate.

    Output is JPEG ≤ `max_edge` px on the long side, RGB, quality 92 — except
    for PNGs that already fit, which are passed through unchanged.
    """
    opened = Image.open(io.BytesIO(candidate.data))
    opened.load()
    width, height = opened.size
    long_edge = max(width, height)
    needs_resize = long_edge > max_edge
    is_png = (candidate.mime or "").lower().endswith("png") or opened.format == "PNG"

    if not needs_resize and is_png:
        return Cover(
            data=candidate.data,
            mime="image/png",
            width=width,
            height=height,
            source=candidate.source,
            label=candidate.label,
        )

    image: Image.Image = opened
    if needs_resize:
        scale = max_edge / long_edge
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
        width, height = image.size

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True, progressive=True)
    return Cover(
        data=buffer.getvalue(),
        mime="image/jpeg",
        width=width,
        height=height,
        source=candidate.source,
        label=candidate.label,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_folder_images(album_dir: Path) -> list[Path]:
    """Find candidate cover-art files in `album_dir`.

    Two-tier match: exact stems (`cover.jpg`, `front.png`) are preferred. As a
    fallback, any image whose stem contains `cover` / `folder` / `front` /
    `albumart` as a token is included — this catches scene-rip naming like
    `*-(front)-*.jpg` and `absolute music 45 front.jpg`. `back` covers are
    filtered so a back-only file doesn't get picked when no front exists.
    """
    if not album_dir.is_dir():
        return []
    matches: list[Path] = []
    seen: set[Path] = set()
    by_stem: dict[str, list[Path]] = {stem: [] for stem in _COVER_STEMS}
    keyword_matches: list[Path] = []
    for entry in album_dir.iterdir():
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix not in _COVER_EXTS:
            continue
        stem = entry.stem.lower()
        if stem in by_stem:
            by_stem[stem].append(entry)
            continue
        # Token-level fallback for noisy filenames.
        if _COVER_KEYWORD_RE.search(stem) and not _BACK_KEYWORD_RE.search(stem):
            keyword_matches.append(entry)
    for stem in _COVER_STEMS:
        for path in sorted(by_stem.get(stem, []), key=lambda p: p.name):
            if path not in seen:
                matches.append(path)
                seen.add(path)
    for path in sorted(keyword_matches, key=lambda p: p.name):
        if path not in seen:
            matches.append(path)
            seen.add(path)
    return matches


def _measure(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return image.size
    except Exception:
        return (0, 0)


def _looks_like_disc_anchor(path: Path) -> bool:
    """True if `path`'s directory name contains a disc-indicator suffix.

    We re-import the regex from `discover` rather than duplicating it so the
    "is this a disc folder?" decision stays in one place.
    """
    from musickit.discover import _DISC_LEAD_RE, _DISC_SUFFIX_RE  # local import to avoid cycle

    name = path.name
    return bool(_DISC_LEAD_RE.match(name) or _DISC_SUFFIX_RE.search(name))


def _guess_mime(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"
