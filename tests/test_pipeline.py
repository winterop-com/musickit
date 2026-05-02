"""Pipeline-level guarantees: atomic album writes, error propagation, cover scan."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from rich.console import Console

from musickit import convert as convert_mod
from musickit import pipeline
from musickit.convert import OutputFormat


@pytest.fixture
def album_inputs(silent_flac_template: Path, tmp_path: Path) -> Path:
    """Layout: tmp/input/Artist/Album/<two flacs with album-tagged metadata>."""
    from mutagen.flac import FLAC

    album_dir = tmp_path / "input" / "Artist Folder"
    album_dir.mkdir(parents=True)
    for n, title in enumerate(["First", "Second"], start=1):
        dst = album_dir / f"{n:02d} - {title}.flac"
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        flac["TITLE"] = title
        flac["ARTIST"] = "Test Artist"
        flac["ALBUMARTIST"] = "Test Artist"
        flac["ALBUM"] = "Test Album"
        flac["DATE"] = "2024"
        flac["TRACKNUMBER"] = str(n)
        flac["TRACKTOTAL"] = "2"
        flac.save()
    return tmp_path


def test_album_write_is_atomic_old_output_survives_failure(
    album_inputs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing track must not delete the previous complete album."""
    out_root = album_inputs / "output"
    # Pre-existing output album from an earlier "successful" run.
    prior_album = out_root / "Test Artist" / "2024 - Test Album"
    prior_album.mkdir(parents=True)
    sentinel = prior_album / "EXISTING.txt"
    sentinel.write_text("preserve me")

    # Make the second encode fail mid-album.
    real_encode = convert_mod.encode
    call_count = {"n": 0}

    def flaky_encode(src: Path, dst: Path, fmt: OutputFormat, *, bitrate: str = "256k") -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise convert_mod.FFmpegFailedError(["ffmpeg"], 1, "synthetic failure")
        real_encode(src, dst, fmt, bitrate=bitrate)

    monkeypatch.setattr(convert_mod, "encode", flaky_encode)

    reports = pipeline.run(
        album_inputs / "input",
        out_root,
        fmt=convert_mod.OutputFormat.ALAC,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert len(reports) == 1
    report = reports[0]

    # Album is reported as failed → CLI exit code will be non-zero.
    assert report.ok is False
    assert report.error is not None and "tracks failed" in report.error

    # Prior output preserved verbatim — sentinel still there, no staging dir leftover.
    assert sentinel.exists() and sentinel.read_text() == "preserve me"
    artist_dir = out_root / "Test Artist"
    assert not (artist_dir / ".2024 - Test Album.staging").exists()
    # No new tracks leaked into the prior album.
    m4a_files = list(prior_album.glob("*.m4a"))
    assert m4a_files == []


def test_album_write_swaps_in_on_full_success(album_inputs: Path) -> None:
    """When every track succeeds, the staging dir replaces the prior album."""
    out_root = album_inputs / "output"
    prior_album = out_root / "Test Artist" / "2024 - Test Album"
    prior_album.mkdir(parents=True)
    (prior_album / "stale.txt").write_text("should be replaced")

    reports = pipeline.run(
        album_inputs / "input",
        out_root,
        fmt=convert_mod.OutputFormat.ALAC,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True
    # Stale file gone, new tracks present, no staging dir leftover.
    assert not (prior_album / "stale.txt").exists()
    assert sorted(p.name for p in prior_album.glob("*.m4a")) == [
        "01 - First.m4a",
        "02 - Second.m4a",
    ]
    artist_dir = out_root / "Test Artist"
    assert not (artist_dir / ".2024 - Test Album.staging").exists()
    assert not (artist_dir / ".2024 - Test Album.backup").exists()


def test_collision_disambiguation_keeps_every_track(silent_flac_template: Path, tmp_path: Path) -> None:
    """Two tracks sharing track-no + title must both end up on disk, not silently dropped."""
    from mutagen.flac import FLAC

    album_dir = tmp_path / "input" / "DupAlbum"
    album_dir.mkdir(parents=True)
    for src_name in ["a.flac", "b.flac"]:
        dst = album_dir / src_name
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        flac["TITLE"] = "Same"
        flac["ARTIST"] = "Solo"
        flac["ALBUMARTIST"] = "Solo"
        flac["ALBUM"] = "Dup Album"
        flac["DATE"] = "2024"
        flac["TRACKNUMBER"] = "1"  # both tagged as track 1 → planned filename collision
        flac.save()

    out_root = tmp_path / "output"
    reports = pipeline.run(
        tmp_path / "input",
        out_root,
        fmt=OutputFormat.ALAC,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True
    out_album = out_root / "Solo" / "2024 - Dup Album"
    files = {p.name for p in out_album.glob("*.m4a")}
    assert len(files) == 2  # neither track silently dropped
    assert files == {"01 - Same.m4a", "01 - Same (2).m4a"}
    # The collision shows up as a warning so the user notices.
    assert any("collision" in w for w in reports[0].warnings)


def test_dry_run_surfaces_duplicate_output_dir_collision(silent_flac_template: Path, tmp_path: Path) -> None:
    """Two input albums normalising to the same output path must surface in --dry-run."""
    from mutagen.flac import FLAC

    for name in ("Source A", "Source B"):
        album_dir = tmp_path / "input" / name
        album_dir.mkdir(parents=True)
        dst = album_dir / "01 - Track.flac"
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        flac["TITLE"] = "Track"
        flac["ARTIST"] = "Solo"
        flac["ALBUMARTIST"] = "Solo"
        flac["ALBUM"] = "Same Album"
        flac["DATE"] = "2024"
        flac["TRACKNUMBER"] = "1"
        flac.save()

    reports = pipeline.run(
        tmp_path / "input",
        tmp_path / "output",
        fmt=OutputFormat.ALAC,
        dry_run=True,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert len(reports) == 2
    oks = [r for r in reports if r.ok]
    failures = [r for r in reports if not r.ok]
    assert len(oks) == 1
    assert len(failures) == 1
    assert failures[0].error == "duplicate output dir"
    # Dry-run wrote nothing to disk.
    assert not (tmp_path / "output").exists()


def test_collision_disambiguation_handles_pre_renamed_collisions(silent_flac_template: Path, tmp_path: Path) -> None:
    """A third track titled `Same (2)` must not clobber the auto-renamed second `Same`."""
    from mutagen.flac import FLAC

    album_dir = tmp_path / "input" / "DupAlbum"
    album_dir.mkdir(parents=True)
    titles = ["Same", "Same", "Same (2)"]
    for i, title in enumerate(titles, start=1):
        dst = album_dir / f"src{i}.flac"
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        flac["TITLE"] = title
        flac["ARTIST"] = "Solo"
        flac["ALBUMARTIST"] = "Solo"
        flac["ALBUM"] = "Dup Album"
        flac["DATE"] = "2024"
        flac["TRACKNUMBER"] = "1"  # all three planned as `01 - …`
        flac.save()

    out_root = tmp_path / "output"
    reports = pipeline.run(
        tmp_path / "input",
        out_root,
        fmt=OutputFormat.ALAC,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True
    out_album = out_root / "Solo" / "2024 - Dup Album"
    files = {p.name for p in out_album.glob("*.m4a")}
    # All three planned destinations resolve to distinct on-disk files.
    assert len(files) == 3
    assert "01 - Same.m4a" in files
    assert "01 - Same (2).m4a" in files
    # The third track is bumped to `(3)` because `(2)` is already reserved.
    assert "01 - Same (2) (2).m4a" in files or "01 - Same (3).m4a" in files


def test_auto_mode_mp3_transcodes_to_aac_m4a(silent_flac_template: Path, tmp_path: Path) -> None:
    """AUTO + MP3 source: transcode to AAC m4a so the library stays uniform.

    Tradeoff: one-time lossy → lossy tandem encode. Win: every file is
    `.m4a` with AAC inside, so Finder/Music.app/everything reads tags
    consistently. Acceptable cost on a Bluetooth-listening chain (which
    re-encodes at 256k AAC anyway).
    """
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4

    album_dir = tmp_path / "input" / "Mixed"
    album_dir.mkdir(parents=True)
    flac_src = album_dir / "01 - Track.flac"
    shutil.copy2(silent_flac_template, flac_src)
    flac = FLAC(flac_src)
    flac["TITLE"] = "Track"
    flac["ARTIST"] = "Test"
    flac["ALBUMARTIST"] = "Test"
    flac["ALBUM"] = "Mixed Album"
    flac["DATE"] = "2025"
    flac["TRACKNUMBER"] = "1"
    flac.save()
    mp3_src = album_dir / "01 - Track.mp3"
    convert_mod.encode(flac_src, mp3_src, OutputFormat.MP3, bitrate="128k")
    flac_src.unlink()

    out_root = tmp_path / "output"
    reports = pipeline.run(
        tmp_path / "input",
        out_root,
        fmt=OutputFormat.AUTO,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True, reports[0].error or reports[0].warnings

    out_album = out_root / "Test" / "2025 - Mixed Album"
    m4a_out = list(out_album.glob("*.m4a"))
    assert len(m4a_out) == 1
    assert not list(out_album.glob("*.mp3"))

    info = MP4(m4a_out[0]).info
    assert info is not None
    # Codec inside MP4 is AAC, not MP3/ALAC — confirms transcode happened.
    codec = str(getattr(info, "codec", "") or "")
    assert codec.startswith("mp4a"), codec
    assert "alac" not in codec.lower()


def test_cover_collects_every_distinct_embedded_picture(silent_flac_template: Path, tmp_path: Path) -> None:
    """Embedded covers from every track are collected, not just the first track's."""
    from mutagen.flac import FLAC, Picture

    from musickit import cover as cover_mod
    from musickit.metadata import read_source

    album_dir = tmp_path / "MixedCovers"
    album_dir.mkdir()

    def jpeg_bytes(width: int, height: int) -> bytes:
        from io import BytesIO

        from PIL import Image

        img = Image.new("RGB", (width, height), color=(width % 256, height % 256, 64))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    # Track 1: small cover. Track 2: larger cover.
    for n, (w, h) in enumerate([(200, 200), (1200, 1200)], start=1):
        dst = album_dir / f"{n:02d} - track.flac"
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.width = w
        pic.height = h
        pic.data = jpeg_bytes(w, h)
        flac.add_picture(pic)
        flac.save()

    tracks = [read_source(p) for p in sorted(album_dir.glob("*.flac"))]
    candidates = cover_mod.collect_candidates(album_dir, tracks)
    embedded = [c for c in candidates if c.source.value == "embedded"]
    assert len(embedded) == 2  # both pictures collected, not just track 1's
    best = cover_mod.pick_best(candidates)
    assert best is not None
    # The 1200×1200 picture wins on pixel area.
    assert best.width == 1200 and best.height == 1200
