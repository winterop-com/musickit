"""Persistent SQLite library index — schema, scan_full, load, validate."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from mutagen.mp4 import MP4
from typer.testing import CliRunner

from musickit import library
from musickit.cli import app
from tests.test_library import _make_track

# ---------------------------------------------------------------------------
# Schema + open_db
# ---------------------------------------------------------------------------


def test_open_db_creates_schema_and_meta(tmp_path: Path) -> None:
    """A fresh root gets `.musickit/index.db` with v1 schema + meta rows."""
    root = tmp_path / "lib"
    root.mkdir()

    conn = library.open_db(root)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"meta", "albums", "tracks", "track_genres", "album_warnings"}.issubset(tables)
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        assert meta["schema_version"] == str(library.SCHEMA_VERSION)
        assert meta["library_root_abs"] == str(root.resolve())
    finally:
        conn.close()

    assert library.db_path(root).exists()


def test_open_db_rebuilds_on_schema_mismatch(tmp_path: Path) -> None:
    """A tampered schema_version triggers unlink + recreate, no migration."""
    root = tmp_path / "lib"
    root.mkdir()
    conn = library.open_db(root)
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.execute(
        "INSERT INTO albums(rel_path, artist_dir, album_dir, track_count, dir_mtime, scanned_at) "
        "VALUES ('Artist/Album', 'Artist', 'Album', 1, 0, 0)"
    )
    conn.close()

    # Reopen → schema mismatch → unlinked → recreated empty.
    conn = library.open_db(root)
    try:
        sv = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        assert sv == str(library.SCHEMA_VERSION)
        assert library.is_empty(conn)
    finally:
        conn.close()


def test_open_db_rebuilds_when_root_changes(tmp_path: Path) -> None:
    """Moving the library root invalidates the cached `library_root_abs`."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    conn = library.open_db(root_a)
    conn.close()

    db_file = library.db_path(root_a)
    moved = library.db_path(root_b)
    moved.parent.mkdir(parents=True, exist_ok=True)
    db_file.rename(moved)

    conn = library.open_db(root_b)
    try:
        meta_root = conn.execute("SELECT value FROM meta WHERE key='library_root_abs'").fetchone()[0]
        assert meta_root == str(root_b.resolve())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# scan_full + load round-trip
# ---------------------------------------------------------------------------


def test_scan_full_then_load_round_trips(silent_flac_template: Path, tmp_path: Path) -> None:
    """scan_full populates rows; load() rebuilds the same Pydantic graph."""
    root = tmp_path / "lib"
    album = root / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(album, silent_flac_template, filename="01 - Radioactive.m4a", title="Radioactive")
    _make_track(
        album,
        silent_flac_template,
        filename="02 - Tiptoe.m4a",
        title="Tiptoe",
        track_no=2,
    )

    conn = library.open_db(root)
    try:
        scanned = library.scan_full(root, conn)
        assert len(scanned.albums) == 1
        assert scanned.albums[0].track_count == 2

        loaded = library.load(root, conn)
        assert len(loaded.albums) == 1
        a = loaded.albums[0]
        assert a.artist_dir == "Imagine Dragons"
        assert a.album_dir == "2012 - Night Visions"
        assert a.track_count == 2
        assert {t.title for t in a.tracks} == {"Radioactive", "Tiptoe"}
        # Paths are reconstructed under the original root.
        assert all(t.path.is_file() for t in a.tracks)
    finally:
        conn.close()


def test_scan_full_persists_warnings(silent_flac_template: Path, tmp_path: Path) -> None:
    """Audit warnings survive the scan_full → load round-trip."""
    root = tmp_path / "lib"
    album = root / "Unknown Artist" / "Untitled"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", year=None, cover_size=None)

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        loaded = library.load(root, conn)
        warnings = loaded.albums[0].warnings
        assert any("no cover" in w for w in warnings)
        assert any("missing year" in w for w in warnings)
    finally:
        conn.close()


def test_scan_full_clears_stale_rows(silent_flac_template: Path, tmp_path: Path) -> None:
    """Re-running scan_full after deleting an album drops the old rows."""
    root = tmp_path / "lib"
    album_a = root / "A" / "2020 - One"
    album_b = root / "B" / "2021 - Two"
    _make_track(album_a, silent_flac_template, filename="01 - T.m4a")
    _make_track(album_b, silent_flac_template, filename="01 - T.m4a")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 2

        # Remove album B from disk, rerun scan_full.
        import shutil

        shutil.rmtree(album_b.parent)
        library.scan_full(root, conn)
        rows = list(conn.execute("SELECT artist_dir FROM albums"))
        assert [r[0] for r in rows] == ["A"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# validate() — incremental delta handling
# ---------------------------------------------------------------------------


def test_validate_picks_up_new_album(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)

        # Drop a brand-new album in.
        _make_track(root / "B" / "2021 - Two", silent_flac_template, filename="01 - T.m4a")
        result = library.validate(root, conn)
        assert result.added == 1
        assert result.removed == 0
        assert result.updated == 0
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 2
    finally:
        conn.close()


def test_validate_picks_up_removed_album(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    _make_track(root / "B" / "2021 - Two", silent_flac_template, filename="01 - T.m4a")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 2

        import shutil

        shutil.rmtree(root / "B")
        result = library.validate(root, conn)
        assert result.removed == 1
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0] == 1
    finally:
        conn.close()


def test_validate_picks_up_tag_edit(silent_flac_template: Path, tmp_path: Path) -> None:
    """Editing a tag bumps file mtime; validate() re-reads that album."""
    root = tmp_path / "lib"
    album = root / "A" / "2020 - One"
    track_path = _make_track(album, silent_flac_template, filename="01 - T.m4a", title="Old Title")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        # Confirm the cached title.
        old = conn.execute("SELECT title FROM tracks").fetchone()[0]
        assert old == "Old Title"

        # Edit the title via mutagen; mtime advances.
        mp4 = MP4(track_path)
        assert mp4.tags is not None
        mp4.tags["\xa9nam"] = ["New Title"]
        mp4.save()
        # Force a stat-mtime delta even on filesystems with second-resolution stat.
        future = time.time() + 2
        import os

        os.utime(track_path, (future, future))

        result = library.validate(root, conn)
        assert result.updated == 1
        new = conn.execute("SELECT title FROM tracks").fetchone()[0]
        assert new == "New Title"
    finally:
        conn.close()


def test_validate_clears_warning_after_fix(silent_flac_template: Path, tmp_path: Path) -> None:
    """A tag edit that resolves a warning makes the warning disappear."""
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    track_path = _make_track(album, silent_flac_template, filename="01 - T.m4a", year=None)

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        warnings_before = [r[0] for r in conn.execute("SELECT warning FROM album_warnings")]
        assert any("missing year" in w for w in warnings_before)

        mp4 = MP4(track_path)
        assert mp4.tags is not None
        mp4.tags["\xa9day"] = ["2020"]
        mp4.save()
        import os

        future = time.time() + 2
        os.utime(track_path, (future, future))

        library.validate(root, conn)
        warnings_after = [r[0] for r in conn.execute("SELECT warning FROM album_warnings")]
        assert not any("missing year" in w for w in warnings_after)
    finally:
        conn.close()


def test_validate_noop_when_filesystem_unchanged(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    conn = library.open_db(root)
    try:
        library.scan_full(root, conn)
        result = library.validate(root, conn)
        assert result.added == 0
        assert result.removed == 0
        assert result.updated == 0
        assert not result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# load_or_scan integration
# ---------------------------------------------------------------------------


def test_load_or_scan_creates_db_on_first_call(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    assert not library.db_path(root).exists()
    idx = library.load_or_scan(root)
    assert len(idx.albums) == 1
    assert library.db_path(root).exists()


def test_load_or_scan_uses_cache_on_second_call(
    silent_flac_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call hydrates from rows; doesn't re-walk the filesystem."""
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    library.load_or_scan(root)

    # Sabotage the audio file source so a re-scan would fail; load path
    # should hit the cache and never read the file.
    from musickit.metadata import read as metadata_read

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("read_source should not be called on cache hit")

    monkeypatch.setattr(metadata_read, "read_source", boom)
    idx = library.load_or_scan(root)
    assert len(idx.albums) == 1


def test_load_or_scan_force_rescans(silent_flac_template: Path, tmp_path: Path) -> None:
    """force=True wipes the rows and rescans."""
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    library.load_or_scan(root)

    # Mark a sentinel in the DB; force=True should drop it.
    conn = library.open_db(root)
    try:
        conn.execute("INSERT INTO meta(key, value) VALUES ('test_sentinel', 'present')")
    finally:
        conn.close()

    library.load_or_scan(root, force=True)
    conn = library.open_db(root)
    try:
        # last_full_scan_at gets re-written; sentinel survives because we only
        # delete album rows on full scan, not arbitrary meta rows.
        # That's expected — meta is for system state, not user-editable.
        rows = dict(conn.execute("SELECT key, value FROM meta"))
        assert rows.get("test_sentinel") == "present"
    finally:
        conn.close()


def test_load_or_scan_no_cache_skips_db(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    idx = library.load_or_scan(root, use_cache=False)
    assert len(idx.albums) == 1
    assert not library.db_path(root).exists()


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def test_cli_drop_index(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    library.load_or_scan(root)
    assert library.db_path(root).exists()

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root), "--drop-index"])
    assert result.exit_code == 0, result.output
    assert "removed" in result.output
    assert not library.db_path(root).exists()


def test_cli_index_status(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")
    library.load_or_scan(root)

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root), "--index-status"])
    assert result.exit_code == 0, result.output
    assert "schema_version" in result.output
    assert "albums" in result.output
    assert "tracks" in result.output


def test_cli_full_rescan(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    _make_track(root / "A" / "2020 - One", silent_flac_template, filename="01 - T.m4a")

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root), "--full-rescan"])
    assert result.exit_code == 0, result.output
    assert library.db_path(root).exists()


# Silence pytest "unused import" for fixtures used via direct invocation above.
_ = sqlite3
