"""`musickit library cover` + `musickit library retag` — destructive file ops.

These commands write to every audio file in a directory. The test fixtures
build a synthetic 2-track album, run the CLI via `typer.testing.CliRunner`,
and verify both the side effects on disk (tags read back via mutagen) and
the exit code on the failure paths.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from mutagen.mp4 import MP4
from PIL import Image
from typer.testing import CliRunner

from musickit.cli import app
from tests.test_library import _make_track

# ---------------------------------------------------------------------------
# library cover
# ---------------------------------------------------------------------------


def _solid_jpeg(path: Path, *, size: tuple[int, int] = (800, 800), color: str = "darkgreen") -> Path:
    img = Image.new("RGB", size, color=color)
    img.save(path, format="JPEG", quality=85)
    return path


def test_library_cover_embeds_into_every_track(silent_flac_template: Path, tmp_path: Path) -> None:
    """Image gets embedded into every audio file in the album dir."""
    album = tmp_path / "lib" / "Imagine Dragons" / "2012 - Night Visions"
    _make_track(album, silent_flac_template, filename="01 - Radioactive.m4a", title="Radioactive", cover_size=None)
    _make_track(album, silent_flac_template, filename="02 - Tiptoe.m4a", title="Tiptoe", track_no=2, cover_size=None)

    cover = _solid_jpeg(tmp_path / "cover.jpg", size=(1000, 1000), color="purple")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "cover", str(cover), str(album)])
    assert result.exit_code == 0, result.output
    assert "embedding" in result.output

    for filename in ("01 - Radioactive.m4a", "02 - Tiptoe.m4a"):
        mp4 = MP4(album / filename)
        assert mp4.tags is not None
        covers = mp4.tags.get("covr") or []
        assert len(covers) == 1, f"{filename} should have exactly one embedded cover"
        # The image was normalised to fit DEFAULT_MAX_EDGE; round-trip
        # decode and confirm it's at least vaguely image-shaped.
        decoded = Image.open(BytesIO(bytes(covers[0])))
        assert decoded.size[0] > 0


def test_library_cover_rejects_non_image_extension(silent_flac_template: Path, tmp_path: Path) -> None:
    """A `.txt` masquerading as a cover bails out with an exit code."""
    album = tmp_path / "lib" / "A" / "2020 - X"
    _make_track(album, silent_flac_template, filename="01 - T.m4a")
    bogus = tmp_path / "not-an-image.txt"
    bogus.write_text("not actually an image")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "cover", str(bogus), str(album)])
    assert result.exit_code != 0


def test_library_cover_rejects_corrupted_jpeg(silent_flac_template: Path, tmp_path: Path) -> None:
    """A `.jpg` file with garbage bytes fails decoding and exits non-zero."""
    album = tmp_path / "lib" / "A" / "2020 - X"
    _make_track(album, silent_flac_template, filename="01 - T.m4a")
    bogus = tmp_path / "broken.jpg"
    bogus.write_bytes(b"\x00" * 256)

    runner = CliRunner()
    result = runner.invoke(app, ["library", "cover", str(bogus), str(album)])
    assert result.exit_code != 0


def test_library_cover_no_audio_files_exits_nonzero(tmp_path: Path) -> None:
    """Empty target dir → no-op with a helpful message and a non-zero exit."""
    album = tmp_path / "empty"
    album.mkdir()
    cover = _solid_jpeg(tmp_path / "cover.jpg")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "cover", str(cover), str(album)])
    assert result.exit_code != 0
    assert "no audio files" in result.output.lower()


def test_library_cover_no_recursive_skips_subdirs(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--no-recursive` only touches the top-level album dir, not subdirs."""
    album = tmp_path / "lib" / "A" / "2020 - X"
    _make_track(album, silent_flac_template, filename="01 - Top.m4a", cover_size=None)
    sub = album / "CD1"
    _make_track(sub, silent_flac_template, filename="01 - Sub.m4a", cover_size=None)

    cover = _solid_jpeg(tmp_path / "cover.jpg")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "cover", str(cover), str(album), "--no-recursive"])
    assert result.exit_code == 0, result.output

    top = MP4(album / "01 - Top.m4a")
    sub_track = MP4(sub / "01 - Sub.m4a")
    assert top.tags and (top.tags.get("covr") or [])
    assert sub_track.tags is not None
    # Subdir track was skipped → no cover written.
    assert not (sub_track.tags.get("covr") or [])


# ---------------------------------------------------------------------------
# library retag
# ---------------------------------------------------------------------------


def test_library_retag_writes_only_passed_fields(silent_flac_template: Path, tmp_path: Path) -> None:
    """Only fields explicitly passed on the CLI are written; others preserved."""
    album = tmp_path / "lib" / "Old Artist" / "1999 - Wrong Year"
    track_path = _make_track(
        album,
        silent_flac_template,
        filename="01 - T.m4a",
        title="Original Title",
        artist="Old Artist",
        album="Wrong Year",
        year="1999",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["library", "retag", str(album), "--year", "2020"])
    assert result.exit_code == 0, result.output

    mp4 = MP4(track_path)
    assert mp4.tags is not None
    assert mp4.tags["\xa9day"] == ["2020"]
    # Title and artist untouched.
    assert mp4.tags["\xa9nam"] == ["Original Title"]
    assert mp4.tags["\xa9ART"] == ["Old Artist"]


def test_library_retag_requires_at_least_one_field(silent_flac_template: Path, tmp_path: Path) -> None:
    """Calling retag with no field flags is a usage error."""
    album = tmp_path / "lib" / "A" / "2020 - X"
    _make_track(album, silent_flac_template, filename="01 - T.m4a")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "retag", str(album)])
    assert result.exit_code != 0
    assert "at least one" in result.output.lower() or "missing" in result.output.lower()


def test_library_retag_with_rename_renames_dir(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--rename` renames the album dir to match the new tags after retagging."""
    album = tmp_path / "lib" / "Artist" / "Wrong Title"
    _make_track(
        album,
        silent_flac_template,
        filename="01 - T.m4a",
        album="Old Album",
        year="2020",
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["library", "retag", str(album), "--album", "Right Album", "--year", "2021", "--rename"],
    )
    assert result.exit_code == 0, result.output

    new_album_dir = tmp_path / "lib" / "Artist" / "2021 - Right Album"
    assert new_album_dir.is_dir()
    assert not album.is_dir()


def test_library_retag_no_recursive_skips_subdirs(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--no-recursive` only retags top-level files, not subdirs."""
    album = tmp_path / "lib" / "Artist" / "2020 - X"
    top = _make_track(album, silent_flac_template, filename="01 - Top.m4a", year="2020")
    sub = album / "CD1"
    sub_track = _make_track(sub, silent_flac_template, filename="01 - Sub.m4a", year="2020")

    runner = CliRunner()
    result = runner.invoke(app, ["library", "retag", str(album), "--year", "2025", "--no-recursive"])
    assert result.exit_code == 0, result.output

    top_tags = MP4(top).tags
    sub_tags = MP4(sub_track).tags
    assert top_tags is not None and top_tags["\xa9day"] == ["2025"]
    # Subdir track preserved at the original year.
    assert sub_tags is not None and sub_tags["\xa9day"] == ["2020"]


def test_library_retag_clear_field_with_empty_string(silent_flac_template: Path, tmp_path: Path) -> None:
    """Passing `--genre ''` clears the tag rather than writing an empty string.

    Per CLAUDE.md / the retag-cover guide: empty-string args explicitly
    clear the tag. The exact post-condition depends on whether mutagen
    treats clear-to-empty as removing the atom or writing an empty list;
    we just assert it doesn't keep the original value.
    """
    album = tmp_path / "lib" / "A" / "2020 - X"
    track = _make_track(album, silent_flac_template, filename="01 - T.m4a")
    # Confirm starting genre.
    pre = MP4(track)
    assert pre.tags is not None
    pre.tags["\xa9gen"] = ["Pop"]
    pre.save()

    runner = CliRunner()
    result = runner.invoke(app, ["library", "retag", str(album), "--genre", ""])
    assert result.exit_code == 0, result.output

    post = MP4(track)
    assert post.tags is not None
    genre_after = post.tags.get("\xa9gen", [""]) or [""]
    assert genre_after[0] != "Pop"


# Silence pytest unused-import noise for fixtures used via direct invocation.
_ = pytest
