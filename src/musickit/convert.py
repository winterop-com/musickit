"""ffmpeg wrapper: re-encode any audio source into the chosen output codec."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from enum import Enum
from pathlib import Path

# Source extensions that always carry lossless audio.
_LOSSLESS_EXTS: frozenset[str] = frozenset({".flac", ".wav", ".aiff", ".aif"})
# Source extensions that always carry lossy audio.
_LOSSY_EXTS: frozenset[str] = frozenset({".mp3", ".ogg", ".opus", ".aac"})
# `.m4a` / `.mp4` containers can hold either ALAC (lossless) or AAC (lossy);
# the actual codec is determined by probing.
_AMBIGUOUS_EXTS: frozenset[str] = frozenset({".m4a", ".mp4", ".m4b"})

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"


class OutputFormat(str, Enum):
    """Supported output codecs."""

    AUTO = "auto"  # uniform 256k AAC m4a — encode lossy/lossless sources, stream-copy AAC m4a
    ALAC = "alac"  # Apple Lossless, .m4a (bit-perfect)
    MP3 = "mp3"  # libmp3lame, .mp3
    AAC = "aac"  # native ffmpeg AAC, .m4a

    @property
    def extension(self) -> str:
        # Everything except plain MP3 ends up in `.m4a`; AUTO is resolved per-track
        # but always produces an `.m4a` output.
        if self is OutputFormat.MP3:
            return ".mp3"
        return ".m4a"

    @property
    def is_lossy(self) -> bool:
        return self in (OutputFormat.MP3, OutputFormat.AAC)


_BITRATE_RE = re.compile(r"^\s*(\d{2,4})\s*k?\s*$", re.IGNORECASE)
DEFAULT_LOSSY_BITRATE = "256k"


class FFmpegMissingError(RuntimeError):
    """Raised when `ffmpeg`/`ffprobe` aren't available on `$PATH`."""


class FFmpegFailedError(RuntimeError):
    """Raised when ffmpeg returns a non-zero exit code."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"ffmpeg failed (exit {returncode}): {' '.join(cmd)}\n{stderr.strip()}")
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


def ensure_ffmpeg() -> None:
    """Verify that `ffmpeg` and `ffprobe` are on `$PATH`. Raise otherwise."""
    missing = [name for name in (FFMPEG_BIN, FFPROBE_BIN) if shutil.which(name) is None]
    if missing:
        raise FFmpegMissingError(
            f"required binaries not on PATH: {', '.join(missing)} (install with `brew install ffmpeg` on macOS)"
        )


def is_lossy_source(path: Path) -> bool:
    """Return True if `path` is a lossy-encoded audio file.

    For unambiguous extensions the answer comes from extension alone. For
    `.m4a`/`.mp4` we probe the codec via mutagen — ALAC is lossless, AAC is
    lossy. Probe failures conservatively classify as lossy (better to over-
    warn than silently transcode a lossless source).
    """
    suffix = path.suffix.lower()
    if suffix in _LOSSLESS_EXTS:
        return False
    if suffix in _LOSSY_EXTS:
        return True
    if suffix in _AMBIGUOUS_EXTS:
        try:
            from mutagen.mp4 import MP4

            info = MP4(path).info
            codec = getattr(info, "codec", "") if info else ""
            # MP4Info.codec is "alac" for ALAC, "mp4a" / "mp4a.40.2" for AAC.
            return not str(codec).lower().startswith("alac")
        except Exception:
            return True
    return True  # unknown extension — assume lossy to err on the safe side


def would_be_lossy_recompress(src: Path, fmt: OutputFormat) -> bool:
    """Return True if encoding `src` to `fmt` is a lossy → lossy tandem encode.

    The pathological case: MP3/AAC source → MP3/AAC target. Encoding to ALAC
    is always allowed because the ALAC container faithfully wraps whatever
    PCM the source decodes to (lossless of lossy is bigger, not worse).
    """
    return fmt.is_lossy and is_lossy_source(src)


def normalize_bitrate(value: str | None) -> str:
    """Coerce `192`, `192k`, ` 256K ` → `192k`. Default to 256k when value is None."""
    if value is None:
        return DEFAULT_LOSSY_BITRATE
    match = _BITRATE_RE.match(value)
    if not match:
        raise ValueError(f"invalid bitrate {value!r} (expected something like '192', '256k', '320k')")
    return f"{match.group(1)}k"


def encode(src: Path, dst: Path, fmt: OutputFormat, *, bitrate: str = DEFAULT_LOSSY_BITRATE) -> None:
    """Re-encode `src` into `dst` using `fmt` (audio only, no embedded picture).

    Cover art is not carried over here — it's re-embedded by `metadata.write_tags`
    using a single normalized image picked by `cover.py`. AUTO must be resolved
    by `auto_resolve()` before calling this.
    """
    if fmt is OutputFormat.AUTO:
        raise ValueError("OutputFormat.AUTO must be resolved per-source before encoding")
    ensure_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temp filename's extension intact so ffmpeg can pick a muxer.
    tmp = dst.with_name(f".{dst.name}.part{dst.suffix}")
    if tmp.exists():
        tmp.unlink()

    cmd: list[str] = [
        FFMPEG_BIN,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:a:0",
        "-vn",
    ]
    if fmt is OutputFormat.ALAC:
        cmd += ["-c:a", "alac", "-f", "ipod"]
    elif fmt is OutputFormat.MP3:
        cmd += ["-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3", "-f", "mp3"]
    elif fmt is OutputFormat.AAC:
        cmd += ["-c:a", "aac", "-b:a", bitrate, "-f", "ipod"]
    cmd.append(str(tmp))

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise FFmpegFailedError(cmd, result.returncode, result.stderr)

    os.replace(tmp, dst)


def copy_passthrough(src: Path, dst: Path) -> None:
    """Byte-for-byte copy of `src` to `dst`. Used for MP3 → MP3 in AUTO mode.

    Avoids any ffmpeg involvement so the audio stream is identical and the
    file remains a plain MP3 (Finder, Music.app and every player read its
    ID3 tags after `metadata.write_id3_tags()` rewrites them).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.part{dst.suffix}")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def remux_to_m4a(src: Path, dst: Path) -> None:
    """Copy `src`'s audio stream into an `.m4a` (MP4) container without re-encoding.

    Used by AUTO mode for AAC sources whose source is already `.m4a` — the
    audio bytes are preserved (`-c:a copy`) and only the container is rewritten
    so we land in a clean, freshly-tagged file.
    """
    ensure_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.part{dst.suffix}")
    if tmp.exists():
        tmp.unlink()

    cmd = [
        FFMPEG_BIN,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:a:0",
        "-c:a",
        "copy",
        "-vn",
        "-f",
        "mp4",  # `-f ipod` rejects MP3 inside MP4; plain mp4 accepts it
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise FFmpegFailedError(cmd, result.returncode, result.stderr)
    os.replace(tmp, dst)


def auto_resolve(src: Path) -> tuple[OutputFormat, bool]:
    """Pick the AUTO-mode action for `src`.

    Returns `(target_format, copy_only)`. When `copy_only` is True the caller
    uses a stream-copy instead of a re-encode. The rules are:

    - lossless source (FLAC / WAV / ALAC m4a) → `AAC, encode`
    - lossy m4a (AAC) → `AAC, copy_only` (clean re-tag, no transcode)
    - MP3 / other lossy → `AAC, encode` (one-time tandem encode so the
      whole library lands in `.m4a` with metadata visible to Finder/Music.app
      and modern AAC's better quality-per-byte; the cost is a single lossy
      pass that's transparent on Bluetooth playback chains)
    """
    suffix = src.suffix.lower()
    if suffix in _LOSSLESS_EXTS:
        return OutputFormat.AAC, False
    if suffix in _AMBIGUOUS_EXTS:
        # ALAC source → encode to AAC; AAC source → copy through.
        return (OutputFormat.AAC, True) if is_lossy_source(src) else (OutputFormat.AAC, False)
    # MP3, OGG, Opus, AAC-as-.aac, etc. — any lossy source we can't stream-copy
    # into m4a cleanly: re-encode to AAC m4a so the whole library is uniform.
    return OutputFormat.AAC, False


def to_alac(src: Path, dst: Path) -> None:
    """Convenience wrapper: re-encode `src` to ALAC `.m4a`."""
    encode(src, dst, OutputFormat.ALAC)
