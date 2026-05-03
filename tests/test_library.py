"""Library scanner + audit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from musickit import convert, library

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(
    album_dir: Path,
    silent_flac: Path,
    *,
    filename: str,
    title: str = "Track",
    artist: str = "Artist",
    album_artist: str | None = "Artist",
    album: str = "Album",
    year: str | None = "2020",
    track_no: int = 1,
    track_total: int = 1,
    disc_no: int = 0,
    disc_total: int = 0,
    cover_size: tuple[int, int] | None = (800, 800),
) -> Path:
    """Encode the session-scope silent FLAC into a tagged .m4a under `album_dir`."""
    album_dir.mkdir(parents=True, exist_ok=True)
    dst = album_dir / filename
    convert.to_alac(silent_flac, dst)

    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    tags = mp4.tags
    assert tags is not None

    tags["\xa9nam"] = [title]
    tags["\xa9ART"] = [artist]
    tags["\xa9alb"] = [album]
    if album_artist:
        tags["aART"] = [album_artist]
    if year:
        tags["\xa9day"] = [year]
    tags["trkn"] = [(track_no, track_total)]
    if disc_no or disc_total:
        tags["disk"] = [(disc_no, disc_total)]

    if cover_size is not None:
        from io import BytesIO

        img = Image.new("RGB", cover_size, color="red")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        tags["covr"] = [MP4Cover(buf.getvalue(), imageformat=MP4Cover.FORMAT_JPEG)]

    mp4.save()
    return dst


def _audit_codes(album: library.LibraryAlbum) -> list[str]:
    """Lowercase warning fragments for substring asserts."""
    return [w.lower() for w in album.warnings]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def test_scan_groups_by_artist_and_album(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    night_visions = root / "Imagine Dragons" / "2012 - Night Visions"
    smoke_mirrors = root / "Imagine Dragons" / "2015 - Smoke + Mirrors"
    _make_track(night_visions, silent_flac_template, filename="01 - Radioactive.m4a", title="Radioactive")
    _make_track(
        smoke_mirrors,
        silent_flac_template,
        filename="01 - Shots.m4a",
        title="Shots",
        album="Smoke + Mirrors",
        year="2015",
    )

    index = library.scan(root)
    assert len(index.albums) == 2
    titles = sorted(a.album_dir for a in index.albums)
    assert titles == ["2012 - Night Visions", "2015 - Smoke + Mirrors"]
    assert all(a.artist_dir == "Imagine Dragons" for a in index.albums)
    assert all(a.track_count == 1 for a in index.albums)


def test_scan_drops_cover_bytes_after_reading(silent_flac_template: Path, tmp_path: Path) -> None:
    """Memory guard — cover bytes must not be retained on the LibraryTrack."""
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=(1000, 1000))

    index = library.scan(root)
    track = index.albums[0].tracks[0]
    assert track.has_cover is True
    assert track.cover_pixels == 1000 * 1000


# ---------------------------------------------------------------------------
# Audit rules
# ---------------------------------------------------------------------------


def test_audit_flags_missing_cover(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=None)

    index = library.scan(root)
    library.audit(index)
    assert any("no cover" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_low_res_cover(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=(200, 200))

    index = library.scan(root)
    library.audit(index)
    assert any("low-res cover" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_missing_year(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "Untitled Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", year=None)

    index = library.scan(root)
    library.audit(index)
    assert any("missing year" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_mixed_years(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T1.m4a", year="2020")
    _make_track(album, silent_flac_template, filename="02 - T2.m4a", year="2021", track_no=2)

    index = library.scan(root)
    library.audit(index)
    assert any("mixed years" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_scene_residue_in_album_dir(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Various Artists" / "2003 - Absolute_Music_45"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", album="Absolute Music 45")

    index = library.scan(root)
    library.audit(index)
    assert any("scene residue in album dir" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_unknown_artist(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Unknown Artist" / "2020 - Mystery"
    _make_track(album, silent_flac_template, filename="01 - T.m4a")

    index = library.scan(root)
    library.audit(index)
    assert any("unknown artist" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_track_gap(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T1.m4a", track_no=1)
    _make_track(album, silent_flac_template, filename="02 - T2.m4a", track_no=2)
    _make_track(album, silent_flac_template, filename="04 - T4.m4a", track_no=4)

    index = library.scan(root)
    library.audit(index)
    assert any("track gaps" in w and "[3]" in w for w in _audit_codes(index.albums[0]))


def test_audit_flags_tag_path_mismatch(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Wrong Title"
    # Tag album says one thing, dir says another — common after a manual rename without retag.
    _make_track(album, silent_flac_template, filename="01 - T.m4a", album="Right Title")

    index = library.scan(root)
    library.audit(index)
    assert any("tag/path mismatch" in w for w in _audit_codes(index.albums[0]))


def test_audit_clean_album_has_no_warnings(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album = root / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(
        album,
        silent_flac_template,
        filename="01 - Radioactive.m4a",
        title="Radioactive",
        artist="Imagine Dragons",
        album_artist="Imagine Dragons",
        album="Night Visions",
        year="2012",
        cover_size=(1000, 1000),
    )

    index = library.scan(root)
    library.audit(index)
    assert index.albums[0].warnings == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_library_command_renders_tree(silent_flac_template: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from musickit.cli import app

    root = tmp_path / "lib"
    album = root / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(album, silent_flac_template, filename="01 - Radioactive.m4a", title="Radioactive")

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root)])
    assert result.exit_code == 0, result.output
    assert "Imagine Dragons" in result.output
    assert "2012 - Night Visions" in result.output


def test_library_command_audit_table_lists_warnings(silent_flac_template: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from musickit.cli import app

    root = tmp_path / "lib"
    album = root / "Unknown Artist" / "2020 - Mystery"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=None, year=None)

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root), "--audit"])
    assert result.exit_code == 0, result.output
    # Both warnings should show in the table.
    assert "no cover" in result.output
    assert "missing year" in result.output


def test_library_command_json_output(silent_flac_template: Path, tmp_path: Path) -> None:
    import json

    from typer.testing import CliRunner

    from musickit.cli import app

    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a")

    runner = CliRunner()
    result = runner.invoke(app, ["library", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert len(payload["albums"]) == 1
    assert payload["albums"][0]["artist_dir"] == "Artist"


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------


def test_fix_backfills_missing_year_via_musicbrainz(silent_flac_template: Path, tmp_path: Path) -> None:
    """A 'missing year' warning triggers MB lookup; tag is written and dir renamed."""
    root = tmp_path / "lib"
    album_dir = root / "Various Artists" / "Absolute Music 70"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Absolute Music 70",
        album_artist="Various Artists",
        year=None,
    )

    def fake_lookup(album: str, artist: str) -> str | None:
        assert album == "Absolute Music 70"
        assert artist == "Various Artists"
        return "2012"

    index = library.scan(root)
    library.audit(index)
    actions = library.fix_index(index, year_lookup=fake_lookup)

    assert any("year ← 2012" in a for a in actions)
    # Folder renamed.
    new_dir = root / "Various Artists" / "2012 - Absolute Music 70"
    assert new_dir.is_dir()
    # Tag persisted.
    tags_path = new_dir / "01 - T.m4a"
    mp4 = MP4(tags_path)
    assert mp4.tags is not None
    assert mp4.tags["\xa9day"] == ["2012"]


def test_fix_dry_run_does_not_write_or_rename(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album_dir = root / "Various Artists" / "Absolute Music 70"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Absolute Music 70",
        album_artist="Various Artists",
        year=None,
    )

    def fake_lookup(album: str, artist: str) -> str | None:
        return "2012"

    index = library.scan(root)
    library.audit(index)
    actions = library.fix_index(index, dry_run=True, year_lookup=fake_lookup)

    assert any("year ← 2012" in a for a in actions)
    # Filesystem untouched.
    assert album_dir.is_dir()
    assert not (root / "Various Artists" / "2012 - Absolute Music 70").exists()
    mp4 = MP4(album_dir / "01 - T.m4a")
    assert mp4.tags is not None
    assert "\xa9day" not in mp4.tags


def test_fix_renames_on_tag_path_mismatch(silent_flac_template: Path, tmp_path: Path) -> None:
    """Tag-says-X but dir-says-Y → rename dir to match the tag."""
    root = tmp_path / "lib"
    album_dir = root / "Artist" / "2020 - Wrong Title"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Right Title",
        year="2020",
    )

    def fake_lookup(album: str, artist: str) -> str | None:
        return None

    index = library.scan(root)
    library.audit(index)
    actions = library.fix_index(index, year_lookup=fake_lookup)

    assert any("renamed dir" in a for a in actions)
    assert (root / "Artist" / "2020 - Right Title").is_dir()


def test_fix_skips_albums_without_warnings(silent_flac_template: Path, tmp_path: Path) -> None:
    root = tmp_path / "lib"
    album_dir = root / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - Radioactive.m4a",
        title="Radioactive",
        artist="Imagine Dragons",
        album_artist="Imagine Dragons",
        album="Night Visions",
        year="2012",
        cover_size=(1000, 1000),
    )

    def fake_lookup(album: str, artist: str) -> str | None:
        raise AssertionError("year_lookup should not be called when there are no warnings")

    index = library.scan(root)
    library.audit(index)
    actions = library.fix_index(index, year_lookup=fake_lookup)
    assert actions == []


def test_fix_does_not_clobber_existing_target_dir(silent_flac_template: Path, tmp_path: Path) -> None:
    """If renaming would overwrite another dir, skip silently."""
    root = tmp_path / "lib"
    album_dir = root / "Artist" / "2020 - Wrong Title"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Right Title",
        year="2020",
    )
    # Pre-existing collision.
    (root / "Artist" / "2020 - Right Title").mkdir(parents=True)

    def fake_lookup(album: str, artist: str) -> str | None:
        return None

    index = library.scan(root)
    library.audit(index)
    library.fix_index(index, year_lookup=fake_lookup)

    # Both dirs still around — no clobber.
    assert album_dir.is_dir()
    assert (root / "Artist" / "2020 - Right Title").is_dir()


# Silence pytest "unused import" via PIL/MP4 in lint-only contexts.
_ = pytest
