"""On-disk footprint for `--remove-source` cleanup."""

from __future__ import annotations

from pathlib import Path

from musickit.discover import AlbumDir


def _input_footprint(album_dir: AlbumDir) -> list[Path]:
    """Return the on-disk dirs to remove for `album_dir` under `--remove-source`.

    Cases:
    - Single-disc album → `[album_dir.path]`.
    - Multi-disc anchored at one shared parent (e.g. bare-leading
      `Album/CD1` + `Album/CD2` where the anchor is `Album/`) →
      `[album_dir.path]`.
    - Shared-prefix multi-disc (`wrapper/Album (CD1)` + `wrapper/Album (CD2)`)
      → escalate to the wrapper IFF the wrapper contains exactly this album's
      disc folders (no other subdirs). With siblings present (e.g. another
      album also under the same wrapper), return the disc folders themselves
      so removal doesn't take siblings down with it.
    """
    if album_dir.disc_total is None:
        return [album_dir.path]
    track_parents = sorted({t.parent for t in album_dir.tracks})
    if len(track_parents) <= 1:
        return [album_dir.path]
    parents_of_parents = {p.parent for p in track_parents}
    if len(parents_of_parents) != 1:
        return list(track_parents)
    wrapper = parents_of_parents.pop()
    try:
        wrapper_subdirs = {p for p in wrapper.iterdir() if p.is_dir()}
    except OSError:
        return list(track_parents)
    if wrapper_subdirs == set(track_parents):
        return [wrapper]
    # Wrapper has unrelated subdirectories — sibling albums likely. Removing
    # the wrapper would delete them; remove only this album's disc folders.
    return list(track_parents)
