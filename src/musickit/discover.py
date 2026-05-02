"""Walk an input tree and group audio files into albums.

A leaf directory containing audio files is one album. Sibling disc subfolders
(`CD1`, `CD 1`, `Disc 1`, `Disk 1`, etc.) are merged: they become a single
multi-disc album anchored at the parent directory, and each track is tagged
with the disc number derived from its subfolder.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from musickit.metadata import SUPPORTED_AUDIO_EXTS

# Disc indicator can appear in three styles:
# - leading bare:    `CD1`, `CD-1`, `CD 2`, `Disc 2`, `Disk3`
# - leading + trail: `CD2 (Bonus Live CD)`, `CD2 - Live In Madrid`
# - trailing suffix: `Album Name (CD1)`, `Album Name [Disc 2]`
_DISC_LEAD_RE = re.compile(r"^(?:cd|disc|disk)[\s\-_]*(\d+)\b\s*(.*)$", re.IGNORECASE)
_DISC_SUFFIX_RE = re.compile(
    r"\s*[\(\[\-]\s*(?:cd|disc|disk)[\s\-_]*(\d+)\s*[\)\]]?\s*$",
    re.IGNORECASE,
)


def _disc_info(name: str) -> tuple[int, str] | None:
    """Return `(disc_number, prefix)` for a disc folder name, else None.

    `prefix` is everything outside the disc indicator. Two folder names are
    discs of the same album when their prefixes match; that's how we group
    `Queen - Live At Wembley '86 (Disc 1)` with `... (Disc 2)` while leaving
    `Queen - A Night At The Opera` alone.
    """
    lead = _DISC_LEAD_RE.match(name)
    if lead:
        # Prefix is "" + any trailing text after the disc number (e.g.
        # `(Bonus Live CD)`). Track the trailing text under the prefix slot
        # so `CD1` and `CD2 (Bonus)` still group together (both prefix="").
        return int(lead.group(1)), ""
    suffix = _DISC_SUFFIX_RE.search(name)
    if suffix:
        return int(suffix.group(1)), name[: suffix.start()].strip()
    return None


class AlbumDir(BaseModel):
    """An input album: tracks possibly spanning multiple disc subfolders."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    tracks: list[Path]
    disc_for_track: dict[str, int] = Field(default_factory=dict)
    disc_total: int | None = None

    def disc_of(self, track: Path) -> int | None:
        """Return the folder-derived disc number for `track`, if multi-disc."""
        return self.disc_for_track.get(track.as_posix())


def discover_albums(root: Path) -> list[AlbumDir]:
    """Find every album under `root`. Merges `CD1`/`CD2`/etc. siblings.

    A "Best Of Classical" wrapper with composer subfolders deliberately
    is NOT coalesced into a single VA album — the per-composer-dir
    output is the right shape for that layout. (One album per artist.)
    """
    if not root.exists():
        return []

    leaves: list[tuple[Path, list[Path]]] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        tracks = sorted(
            Path(current) / name
            for name in filenames
            if Path(name).suffix.lower() in SUPPORTED_AUDIO_EXTS and not name.startswith(".")
        )
        if tracks:
            leaves.append((Path(current), tracks))

    return _merge_disc_siblings(leaves)


def _merge_disc_siblings(leaves: list[tuple[Path, list[Path]]]) -> list[AlbumDir]:
    """Group disc-subfolder siblings into one AlbumDir each.

    Three cases:
    1. Parent owns audio AND has disc subfolders → drop the disc subfolders
       (typically duplicates or bonus rips). Avoids racing two albums onto
       the same output path.
    2. Otherwise group disc-bearing siblings by their shared prefix and merge
       each group of ≥2 into one multi-disc album. Mixed-content parents are
       fine: only the matching-prefix disc pairs get merged, everything else
       passes through as standalone albums.
    """
    leaf_paths = {path for path, _ in leaves}
    leaf_tracks = {path: tracks for path, tracks in leaves}

    disc_info_for: dict[Path, tuple[int, str]] = {}
    disc_children: dict[Path, list[Path]] = {}
    for path, _ in leaves:
        info = _disc_info(path.name)
        if info is not None:
            disc_info_for[path] = info
            disc_children.setdefault(path.parent, []).append(path)

    consumed: set[Path] = set()
    merged: list[AlbumDir] = []

    for parent, discs in disc_children.items():
        if parent in leaf_paths:
            # Case 1: parent owns audio — drop disc dirs to avoid duplicates.
            consumed.update(discs)
            continue
        # Case 2: group by shared prefix.
        groups: dict[str, list[Path]] = {}
        for p in discs:
            groups.setdefault(disc_info_for[p][1], []).append(p)
        for prefix, group in groups.items():
            if len(group) < 2:
                continue  # singleton — don't merge, leave as standalone
            sorted_group = sorted(group, key=lambda p: disc_info_for[p][0])
            # Anchor at the parent for bare leading style (`CD1`/`CD2`) so
            # the cover at the parent level is reachable. For shared-prefix
            # style (`Album (CD1)`/`Album (CD2)`), anchor at the first disc
            # folder — its name minus the disc indicator gives a usable album
            # name fallback, and any per-disc cover lives inside it.
            anchor = parent if prefix == "" else sorted_group[0]
            merged.append(
                AlbumDir(
                    path=anchor,
                    tracks=[t for disc_path in sorted_group for t in leaf_tracks[disc_path]],
                    disc_for_track={
                        track.as_posix(): disc_info_for[disc_path][0]
                        for disc_path in sorted_group
                        for track in leaf_tracks[disc_path]
                    },
                    disc_total=max(disc_info_for[p][0] for p in group),
                )
            )
            consumed.update(group)

    albums = list(merged)
    merged_paths = {a.path for a in merged}
    for path, tracks in leaves:
        if path in consumed or path in merged_paths:
            continue
        albums.append(AlbumDir(path=path, tracks=tracks))

    albums.sort(key=lambda a: a.path.as_posix())
    return albums
