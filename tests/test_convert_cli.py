"""`musickit convert` CLI end-to-end tests.

The underlying convert helpers (encode, remux, normalize) are exercised
in `test_convert.py`. These tests drive the actual `musickit convert`
typer command via `CliRunner` against silent-FLAC fixtures, verifying
that the wired-together pipeline produces the expected output tree
with proper tags, and that the CLI flags (`--dry-run`, `--format`,
`--no-enrich`) behave as documented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from typer.testing import CliRunner

from musickit.cli import app


def _stage_input_album(input_root: Path, *, artist: str, album: str, year: str, silent_flac: Path) -> Path:
    """Build a 2-track album of FLACs with proper tags under `input_root`."""
    album_dir = input_root / artist / album
    album_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        dst = album_dir / f"{n:02d} - Track{n}.flac"
        # mutagen writes tags onto a copied FLAC; re-use _make_track which
        # is built for m4a — easier to just write a FLAC directly here.
        import shutil

        shutil.copy2(silent_flac, dst)
        flac = FLAC(dst)
        flac["title"] = [f"Track{n}"]
        flac["artist"] = [artist]
        flac["albumartist"] = [artist]
        flac["album"] = [album]
        flac["date"] = [year]
        flac["tracknumber"] = [str(n)]
        flac["tracktotal"] = ["2"]
        flac.save()
    return album_dir


def test_convert_produces_m4a_tree(silent_flac_template: Path, tmp_path: Path) -> None:
    """Default `--format auto` re-encodes every FLAC into a tagged `.m4a`."""
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _stage_input_album(
        input_root,
        artist="Imagine Dragons",
        album="Night Visions",
        year="2012",
        silent_flac=silent_flac_template,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["convert", str(input_root), str(output_root), "--no-enrich"],
    )
    assert result.exit_code == 0, result.output

    out_album = output_root / "Imagine Dragons" / "2012 - Night Visions"
    assert out_album.is_dir(), f"expected output album dir at {out_album}; got tree: {list(output_root.rglob('*'))}"
    m4as = sorted(out_album.glob("*.m4a"))
    assert len(m4as) == 2, f"expected 2 m4a tracks, found {len(m4as)}"

    # Tags survived the FLAC → AAC re-encode.
    mp4 = MP4(m4as[0])
    assert mp4.tags is not None
    assert mp4.tags.get("\xa9ART") == ["Imagine Dragons"]
    assert mp4.tags.get("\xa9alb") == ["Night Visions"]
    assert mp4.tags.get("\xa9day") == ["2012"]


def test_convert_dry_run_writes_nothing(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--dry-run` plans but doesn't touch the output dir."""
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _stage_input_album(
        input_root,
        artist="A",
        album="X",
        year="2020",
        silent_flac=silent_flac_template,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["convert", str(input_root), str(output_root), "--no-enrich", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    # Output dir may exist as an empty placeholder; what matters is no
    # actual track files landed.
    if output_root.exists():
        assert not list(output_root.rglob("*.m4a")), "dry-run should not write track files"


def test_convert_alac_format_produces_lossless(silent_flac_template: Path, tmp_path: Path) -> None:
    """`--format alac` writes Apple Lossless inside the m4a container."""
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _stage_input_album(
        input_root,
        artist="A",
        album="X",
        year="2020",
        silent_flac=silent_flac_template,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["convert", str(input_root), str(output_root), "--format", "alac", "--no-enrich"],
    )
    assert result.exit_code == 0, result.output

    out_album = output_root / "A" / "2020 - X"
    m4as = sorted(out_album.glob("*.m4a"))
    assert len(m4as) >= 1
    # ALAC inside MP4 — mutagen reports `info.codec` accordingly.
    mp4 = MP4(m4as[0])
    codec = (getattr(mp4.info, "codec", "") or "").lower()
    assert "alac" in codec, f"expected ALAC codec, got {codec!r}"


# Silence unused-import lint noise in lint-only mode.
_ = pytest
