"""Rename an album dir on disk to match its tags.

Used by the TUI tag editor (and reusable by the CLI) so changing
`tag_album` / `tag_album_artist` / `tag_year` doesn't leave the on-disk
folder name out of sync. Mirrors the convention `library/convert`
writes — `<root>/<artist_dir>/YYYY - <album_title>/<files>` — by
delegating to `naming.album_folder` for the trailing dirname.

Two-axis rename: the album folder's *name* (`YYYY - Title`) tracks the
album/year tags, AND the album's parent (the artist directory) tracks
`tag_album_artist`. So a tag edit that flips `Daft Punk` → `Daft Punk
& Friends` moves the album across artist dirs in one operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from musickit import naming
from musickit.library.models import LibraryAlbum

log = logging.getLogger(__name__)


class RenameError(Exception):
    """Raised when the rename target collides with an existing path or fs IO fails."""


@dataclass(frozen=True, slots=True)
class RenameResult:
    """What the rename actually did. Use `.changed` to short-circuit no-op cases."""

    old_path: Path
    new_path: Path

    @property
    def changed(self) -> bool:
        return self.old_path.resolve() != self.new_path.resolve()


def compute_new_album_path(album: LibraryAlbum, library_root: Path) -> Path:
    """Return the path the album SHOULD live at given its current tag fields.

    Uses `tag_album_artist` for the parent dir (falls back to `artist_dir`
    when the tag is empty) and `naming.album_folder(tag_album, tag_year)`
    for the leaf. Returns the existing path when the relevant tags are
    missing — caller should treat `result == album.path` as "no change."
    """
    artist_dir = album.tag_album_artist or album.artist_dir
    if not album.tag_album:
        return album.path
    leaf = naming.album_folder(album.tag_album, album.tag_year)
    return library_root / artist_dir / leaf


def rename_album_to_match_tags(album: LibraryAlbum, library_root: Path) -> RenameResult:
    """Rename `album.path` (and update in-memory paths) to match the album's tags.

    Mutates `album.path` and every `track.path` inside it so subsequent
    cache lookups / playback / scan-validate pass land at the new
    location. Raises `RenameError` when the target dir already exists
    (collision with another album) or when the filesystem rename fails.

    No-op if the path is already correct — returns `RenameResult` with
    `.changed=False`.
    """
    new_path = compute_new_album_path(album, library_root)
    old_path = album.path

    if new_path.resolve() == old_path.resolve():
        return RenameResult(old_path=old_path, new_path=new_path)

    if new_path.exists():
        raise RenameError(f"target already exists: {new_path}")

    if not old_path.exists():
        raise RenameError(f"source does not exist: {old_path}")

    # Make sure the new artist dir exists. parent.mkdir is no-op when
    # already present; only fires for cross-artist moves.
    new_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        old_path.rename(new_path)
    except OSError as exc:
        raise RenameError(f"rename {old_path} → {new_path} failed: {exc}") from exc

    # Patch the in-memory album + every track. Track paths are
    # `<old_album_dir>/<filename>` → reroot the parent.
    album.path = new_path
    album.artist_dir = new_path.parent.name
    album.album_dir = new_path.name
    for track in album.tracks:
        try:
            relative = track.path.relative_to(old_path)
        except ValueError:
            # Track lives outside the album dir (synthetic path, edge case).
            # Leave its path alone.
            continue
        track.path = new_path / relative

    log.info("renamed album %s → %s", old_path, new_path)
    return RenameResult(old_path=old_path, new_path=new_path)
