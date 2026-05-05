"""M3U8 read / write — extended M3U with `#EXTINF` lines.

Standard format every audio player understands. We always write
relative paths (relative to the playlist file's directory) so the
playlist stays valid as long as the library tree is moved as a unit.
"""

from __future__ import annotations

from pathlib import Path

from musickit.library.models import LibraryTrack
from musickit.playlist.build import PlaylistResult


def write_m3u8(result: PlaylistResult, out_path: Path) -> Path:
    """Write `result.tracks` as an extended M3U at `out_path`.

    Creates parent directories as needed. Paths inside the file are
    written relative to `out_path.parent`. Returns the resolved path.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = out_path.parent.resolve()

    lines: list[str] = ["#EXTM3U", f"#PLAYLIST:{result.name}"]
    for track in result.tracks:
        try:
            # `walk_up=True` (Py 3.12+) lets the result climb out of
            # `base` via `..`, which is what every audio player expects
            # when the playlist lives in a sibling subtree (e.g.
            # `<lib>/.musickit/playlists/mix.m3u8` referring to
            # `<lib>/Artist/Album/01.m4a`).
            rel = track.path.resolve().relative_to(base, walk_up=True)
            target = str(rel)
        except ValueError:
            # Path is on a different drive / unrelated root — fall back
            # to absolute. (`walk_up=True` only raises in this case.)
            target = str(track.path.resolve())
        lines.append(_extinf(track))
        lines.append(target)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _extinf(track: LibraryTrack) -> str:
    """Build the `#EXTINF:<seconds>,<artist> - <title>` header line."""
    duration = int(round(float(track.duration_s or 0.0)))
    title = track.title or track.path.stem
    artist = track.artist or track.album_artist or ""
    label = f"{artist} - {title}" if artist else title
    return f"#EXTINF:{duration},{label}"


def read_m3u8(path: Path) -> list[Path]:
    """Return the audio paths referenced by the playlist file.

    Resolves relative entries against the playlist file's directory;
    absolute entries are returned as-is. Comment / `#EXTINF` lines are
    skipped — only the path lines are returned.
    """
    base = path.parent.resolve()
    out: list[Path] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        out.append(candidate)
    return out
