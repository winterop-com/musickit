"""validate(): rename within an album + move-file across album dirs.

Both flows go through `validate()` -> set-difference detection ->
`rescan_albums(...)`. The existing `test_validate_picks_up_tag_edit`
covers the in-place mtime-change path; these tests fill the rename
and cross-album move paths the audit flagged as untested.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from musickit import library
from tests.test_library import _make_track


def test_validate_picks_up_renamed_track(silent_flac_template: Path, tmp_path: Path) -> None:
    """Renaming a file within its album dir → set-difference triggers re-scan of that album."""
    root = tmp_path / "lib"
    album_dir = root / "Artist" / "2020 - Album"
    src = _make_track(album_dir, silent_flac_template, filename="01 - Old Name.m4a", title="Old")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)

        before = {row[0] for row in conn.execute("SELECT rel_path FROM tracks")}
        assert any("Old Name" in p for p in before)

        dst = album_dir / "01 - New Name.m4a"
        src.rename(dst)

        result = library.validate(root, conn)
        # The album row is dropped + reinserted (counts as updated).
        assert result.updated == 1
        assert result.added == 0
        assert result.removed == 0

        after = {row[0] for row in conn.execute("SELECT rel_path FROM tracks")}
        assert any("New Name" in p for p in after)
        assert not any("Old Name" in p for p in after)
    finally:
        conn.close()


def test_validate_picks_up_file_moved_across_albums(silent_flac_template: Path, tmp_path: Path) -> None:
    """A track relocated to a different album dir should rescan BOTH dirs."""
    root = tmp_path / "lib"
    album_a = root / "Artist" / "2020 - Album A"
    album_b = root / "Artist" / "2021 - Album B"
    src = _make_track(album_a, silent_flac_template, filename="01 - Wandering.m4a", title="Wandering")
    _make_track(album_b, silent_flac_template, filename="01 - Stable.m4a", title="Stable")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        before_a = conn.execute(
            "SELECT COUNT(*) FROM tracks t JOIN albums a ON t.album_id = a.id WHERE a.album_dir = ?",
            ("2020 - Album A",),
        ).fetchone()[0]
        before_b = conn.execute(
            "SELECT COUNT(*) FROM tracks t JOIN albums a ON t.album_id = a.id WHERE a.album_dir = ?",
            ("2021 - Album B",),
        ).fetchone()[0]
        assert before_a == 1
        assert before_b == 1

        # Move the file across album dirs.
        dst = album_b / "02 - Wandering.m4a"
        shutil.move(str(src), str(dst))

        result = library.validate(root, conn)
        # Both albums are affected -> two updated rescans.
        assert result.updated == 2

        after_a = conn.execute(
            "SELECT COUNT(*) FROM tracks t JOIN albums a ON t.album_id = a.id WHERE a.album_dir = ?",
            ("2020 - Album A",),
        ).fetchone()[0]
        after_b = conn.execute(
            "SELECT COUNT(*) FROM tracks t JOIN albums a ON t.album_id = a.id WHERE a.album_dir = ?",
            ("2021 - Album B",),
        ).fetchone()[0]
        assert after_a == 0
        assert after_b == 2
    finally:
        conn.close()


def test_validate_picks_up_album_dir_renamed(silent_flac_template: Path, tmp_path: Path) -> None:
    """Renaming an album dir = old dir disappears + new dir appears."""
    root = tmp_path / "lib"
    old_album = root / "Artist" / "2020 - Old Title"
    _make_track(old_album, silent_flac_template, filename="01 - T.m4a")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        new_album = root / "Artist" / "2020 - New Title"
        old_album.rename(new_album)

        result = library.validate(root, conn)
        # Old dir vanished + new dir surfaced => one removed + one added.
        assert result.removed == 1
        assert result.added == 1

        rows = {row[0] for row in conn.execute("SELECT album_dir FROM albums")}
        assert "2020 - New Title" in rows
        assert "2020 - Old Title" not in rows
    finally:
        conn.close()


def test_validate_handles_multiple_renames_in_same_album(silent_flac_template: Path, tmp_path: Path) -> None:
    """Two renames within one album → still one rescan of that album."""
    root = tmp_path / "lib"
    album_dir = root / "Artist" / "2020 - Album"
    src1 = _make_track(album_dir, silent_flac_template, filename="01 - One.m4a", title="One")
    src2 = _make_track(album_dir, silent_flac_template, filename="02 - Two.m4a", title="Two", track_no=2)

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)

        src1.rename(album_dir / "01 - One Renamed.m4a")
        src2.rename(album_dir / "02 - Two Renamed.m4a")

        result = library.validate(root, conn)
        assert result.updated == 1  # one album, even though two files changed

        names = {row[0] for row in conn.execute("SELECT rel_path FROM tracks")}
        assert any("One Renamed" in p for p in names)
        assert any("Two Renamed" in p for p in names)
    finally:
        conn.close()
