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


def test_scan_measures_cover_pixels_when_requested(silent_flac_template: Path, tmp_path: Path) -> None:
    """`scan(measure_pictures=True)` populates `cover_pixels` for MP4/M4A.

    The library audit's low-res-cover rule depends on this; without it, the
    rule was effectively disabled for the converter's default output format
    because light mode always wrote `cover_pixels=0`.
    """
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=(800, 600))

    index = library.scan(root, measure_pictures=True)
    track = index.albums[0].tracks[0]
    assert track.has_cover is True
    assert track.cover_pixels == 800 * 600


def test_scan_detects_cover_presence_in_light_mode(silent_flac_template: Path, tmp_path: Path) -> None:
    """Light scan reports cover presence without decoding pixel dimensions.

    `cover_pixels` stays 0 in light mode (no Pillow decode) — that's the
    memory + speed guarantee the TUI relies on. Presence detection still
    works because mutagen has already parsed the tag block.
    """
    root = tmp_path / "lib"
    album = root / "Artist" / "2020 - Album"
    _make_track(album, silent_flac_template, filename="01 - T.m4a", cover_size=(1000, 1000))

    index = library.scan(root)
    track = index.albums[0].tracks[0]
    assert track.has_cover is True
    assert track.cover_pixels == 0  # light scan skips Pillow decode


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


def test_audit_flags_low_res_cover(tmp_path: Path) -> None:
    """Audit rule fires when cover_pixels is populated AND below the threshold.

    Library scan runs in light mode so cover_pixels=0 by default — this
    test wires the album directly so the audit rule can be exercised
    independent of the scan's perf trade-off.
    """
    album = library.LibraryAlbum(
        path=tmp_path,
        artist_dir="Artist",
        album_dir="2020 - Album",
        has_cover=True,
        cover_pixels=200 * 200,
        track_count=1,
        tag_album="Album",
        tag_year="2020",
    )
    library._audit_cover(album)
    assert any("low-res cover" in w for w in album.warnings)


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


def test_audit_does_not_flag_continuous_numbering_across_discs() -> None:
    """Mega-comp convention: disc 2's track 1 is numbered 10, etc. — not a gap."""
    from musickit.library.audit import _audit_track_gaps
    from musickit.library.models import LibraryAlbum, LibraryTrack

    album = LibraryAlbum(
        path=Path("/tmp/x"),
        artist_dir="VA",
        album_dir="Mega Comp",
        tracks=[
            *[LibraryTrack(path=Path(f"/tmp/x/{i}.m4a"), track_no=i, disc_no=1) for i in range(1, 10)],
            *[LibraryTrack(path=Path(f"/tmp/x/{i}.m4a"), track_no=i, disc_no=2) for i in range(10, 19)],
            *[LibraryTrack(path=Path(f"/tmp/x/{i}.m4a"), track_no=i, disc_no=3) for i in range(19, 28)],
        ],
    )
    _audit_track_gaps(album)
    assert album.warnings == []


def test_audit_still_flags_real_gaps_within_continuous_disc() -> None:
    """Continuous numbering doesn't suppress real holes WITHIN a disc's range."""
    from musickit.library.audit import _audit_track_gaps
    from musickit.library.models import LibraryAlbum, LibraryTrack

    album = LibraryAlbum(
        path=Path("/tmp/x"),
        artist_dir="VA",
        album_dir="Mega Comp",
        tracks=[
            *[LibraryTrack(path=Path(f"/tmp/x/{i}.m4a"), track_no=i, disc_no=1) for i in (1, 2, 3, 5, 6, 7, 8, 9)],
            # disc 1 is missing track 4 → still flagged.
            *[LibraryTrack(path=Path(f"/tmp/x/{i}.m4a"), track_no=i, disc_no=2) for i in range(10, 19)],
        ],
    )
    _audit_track_gaps(album)
    assert any("missing [4]" in w for w in album.warnings)


def test_audit_per_disc_starting_at_1_still_flags_gaps_from_1() -> None:
    """Albums where each disc restarts at 1 still get the original behaviour."""
    from musickit.library.audit import _audit_track_gaps
    from musickit.library.models import LibraryAlbum, LibraryTrack

    album = LibraryAlbum(
        path=Path("/tmp/x"),
        artist_dir="A",
        album_dir="Album",
        tracks=[
            # Disc 1: tracks 2, 3 — missing 1
            LibraryTrack(path=Path("/tmp/x/1.m4a"), track_no=2, disc_no=1),
            LibraryTrack(path=Path("/tmp/x/2.m4a"), track_no=3, disc_no=1),
            # Disc 2: tracks 1, 2 — restarts at 1, no gap
            LibraryTrack(path=Path("/tmp/x/3.m4a"), track_no=1, disc_no=2),
            LibraryTrack(path=Path("/tmp/x/4.m4a"), track_no=2, disc_no=2),
        ],
    )
    _audit_track_gaps(album)
    # Disc 1 should be flagged for missing 1; disc 2 is fine.
    assert any("disc 1" in w and "missing [1]" in w for w in album.warnings)
    assert not any("disc 2" in w for w in album.warnings)


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
    result = runner.invoke(app, ["library", "tree", str(root)])
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
    result = runner.invoke(app, ["library", "audit", str(root)])
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
    result = runner.invoke(app, ["library", "tree", str(root), "--json"])
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


def test_fix_prefer_dirname_writes_tag_from_dir(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--prefer-dirname` inverts the mismatch fix: write tag ← dir name."""
    root = tmp_path / "lib"
    album_dir = root / "Artist" / "1983 - Now That's What I Call Music! 01"
    _make_track(
        album_dir,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Now That's What I Call Music",  # no `!`, no number — needs to follow the dir
        year="1983",
    )

    def fake_lookup(album: str, artist: str) -> str | None:
        return None

    index = library.scan(root)
    library.audit(index)
    actions = library.fix_index(index, year_lookup=fake_lookup, prefer_dirname=True)

    assert any("tag ← album=" in a for a in actions)
    # Filesystem dir untouched — only the tag changed.
    assert album_dir.is_dir()
    mp4 = MP4(album_dir / "01 - T.m4a")
    assert mp4.tags is not None
    assert mp4.tags["\xa9alb"] == ["Now That's What I Call Music! 01"]


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


def test_fix_on_album_callback_fires_only_for_flagged(silent_flac_template: Path, tmp_path: Path) -> None:
    """`fix_index(on_album=...)` reports progress over flagged albums only — clean ones are silent."""
    root = tmp_path / "lib"

    # Flagged album (missing year).
    _make_track(
        root / "Various Artists" / "Absolute Music 70",
        silent_flac_template,
        filename="01 - T.m4a",
        album="Absolute Music 70",
        album_artist="Various Artists",
        year=None,
    )
    # Clean album.
    _make_track(
        root / "Imagine Dragons" / "2012 - Night Visions",
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
        return "2012"

    seen: list[tuple[str, int, int]] = []

    def on_album(album: library.LibraryAlbum, idx: int, total: int) -> None:
        seen.append((album.album_dir, idx, total))

    index = library.scan(root)
    library.audit(index)
    library.fix_index(index, year_lookup=fake_lookup, on_album=on_album)

    # Only the flagged album was reported, with a 1/1 total.
    assert len(seen) == 1
    assert seen[0][0] == "Absolute Music 70"
    assert seen[0][1] == 1
    assert seen[0][2] == 1


# Silence pytest "unused import" via PIL/MP4 in lint-only contexts.
_ = pytest
