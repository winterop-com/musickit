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
        overwrite=True,  # exercise the atomic-swap path; default no-replace would just skip
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
        overwrite=True,  # exercising the swap path; default no-replace would skip
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


def test_default_skips_when_album_already_exists_in_output(album_inputs: Path) -> None:
    """Without --overwrite, a pre-existing album dir is preserved + the run skips."""
    out_root = album_inputs / "output"
    prior_album = out_root / "Test Artist" / "2024 - Test Album"
    prior_album.mkdir(parents=True)
    (prior_album / "EXISTING.m4a").write_text("not really an m4a, just a sentinel")

    reports = pipeline.run(
        album_inputs / "input",
        out_root,
        fmt=convert_mod.OutputFormat.ALAC,
        verbose=True,
        console=Console(record=True, width=120),
    )
    report = reports[0]
    # Failed = "skipped because already exists" — surfaces in the summary as not-ok
    # so the CLI exits non-zero (you'll know about it without grepping warnings).
    assert report.ok is False
    assert report.error == "album already exists"
    # Prior contents untouched.
    assert (prior_album / "EXISTING.m4a").exists()
    # No new files leaked into the album.
    assert sorted(p.name for p in prior_album.iterdir()) == ["EXISTING.m4a"]


def test_remove_source_after_successful_album(album_inputs: Path) -> None:
    """`--remove-source` deletes the source dir per album, on success only."""
    input_root = album_inputs / "input"
    out_root = album_inputs / "output"
    src_album = input_root / "Artist Folder"
    assert src_album.exists()  # sanity

    reports = pipeline.run(
        input_root,
        out_root,
        fmt=convert_mod.OutputFormat.ALAC,
        remove_source=True,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True
    # Output exists, source dir is gone.
    assert (out_root / "Test Artist" / "2024 - Test Album").is_dir()
    assert not src_album.exists()


def test_input_footprint_keeps_wrapper_when_dedicated_to_one_album(tmp_path: Path) -> None:
    """Bare-leading + shared-prefix dedicated wrappers escalate to the wrapper."""
    from musickit.discover import AlbumDir
    from musickit.pipeline import _input_footprint

    wrapper = tmp_path / "Album X"
    cd1 = wrapper / "Album X (CD1)"
    cd2 = wrapper / "Album X (CD2)"
    for d in (cd1, cd2):
        d.mkdir(parents=True)
    a1 = cd1 / "01.flac"
    a2 = cd2 / "01.flac"
    a1.touch()
    a2.touch()
    album_dir = AlbumDir(path=cd1, tracks=[a1, a2], disc_total=2)

    paths = _input_footprint(album_dir)
    assert paths == [wrapper]


def test_input_footprint_refuses_wrapper_when_siblings_present(tmp_path: Path) -> None:
    """If the wrapper holds OTHER albums too, fall back to the disc folders.

    Real-world layout:
        Box/
          Album A (CD1)/  <- this album, disc 1
          Album A (CD2)/  <- this album, disc 2
          Album B (CD1)/  <- sibling album
          Album B (CD2)/  <- sibling album

    Removing Box/ would take Album B with it. The footprint must return
    only Album A's disc folders.
    """
    from musickit.discover import AlbumDir
    from musickit.pipeline import _input_footprint

    box = tmp_path / "Box"
    a1 = box / "Album A (CD1)"
    a2 = box / "Album A (CD2)"
    b1 = box / "Album B (CD1)"
    b2 = box / "Album B (CD2)"
    for d in (a1, a2, b1, b2):
        d.mkdir(parents=True)
    a1_track = a1 / "01.flac"
    a2_track = a2 / "01.flac"
    a1_track.touch()
    a2_track.touch()
    album_dir = AlbumDir(path=a1, tracks=[a1_track, a2_track], disc_total=2)

    paths = _input_footprint(album_dir)
    assert sorted(paths) == [a1, a2]
    assert box not in paths  # wrapper protected


def test_remove_source_refuses_to_delete_input_root(silent_flac_template: Path, tmp_path: Path) -> None:
    """If the album footprint resolves to the input root itself, refuse."""
    from mutagen.flac import FLAC

    # Album is at the input ROOT (no enclosing artist/album dir).
    input_root = tmp_path / "input"
    input_root.mkdir()
    dst = input_root / "01 - Track.flac"
    shutil.copy2(silent_flac_template, dst)
    flac = FLAC(dst)
    flac["TITLE"] = "Track"
    flac["ARTIST"] = "Solo"
    flac["ALBUMARTIST"] = "Solo"
    flac["ALBUM"] = "Album"
    flac["DATE"] = "2024"
    flac["TRACKNUMBER"] = "1"
    flac.save()

    out_root = tmp_path / "output"
    reports = pipeline.run(
        input_root,
        out_root,
        fmt=convert_mod.OutputFormat.ALAC,
        remove_source=True,
        verbose=True,
        console=Console(record=True, width=120),
    )
    assert reports[0].ok is True
    # Input root still there — refuse-to-remove kicked in.
    assert input_root.exists()
    assert any("refusing to remove" in w for w in reports[0].warnings)


def test_humanise_slug_cleans_snake_case_titles() -> None:
    """Filename slugs (snake_case lowercase) → human-readable Title Case."""
    from musickit.pipeline import _humanise_slug

    # Plain slug with scene tag suffix.
    assert (
        _humanise_slug("miio_feat_daddy_boastin_-_nar_vi_tva_blir_en-atm")
        == "Miio Feat Daddy Boastin - Nar Vi Tva Blir En"
    )
    # Per Gessle case (no scene tag).
    assert _humanise_slug("per_gessle_-_tycker_om_nar_du_tar_pa_mig") == "Per Gessle - Tycker Om Nar Du Tar Pa Mig"
    # Apostrophe survival: `str.title()` would mangle "don't" → "Don'T".
    assert _humanise_slug("eamon_-_fuck_it_(i_don't_want_you_back)") == "Eamon - Fuck It (i Don't Want You Back)"
    # Already humanised → idempotent.
    assert _humanise_slug("Already Clean Title") == "Already Clean Title"
    # Empty / None-like.
    assert _humanise_slug("") == ""


def test_scene_encoded_dtt_track_number_splits_into_multidisc(silent_flac_template: Path, tmp_path: Path) -> None:
    """`101 = CD1 track 1`-style scene compilations get rewritten to proper multi-disc.

    The Absolute Music / Now!-series convention: track numbers `101..120` then
    `201..220` mean "disc 1 tracks 1-20" + "disc 2 tracks 1-20", with no
    `disc`/`disctotal` tag in sight. Pipeline detects the cluster, rewrites
    `disc_no` / `track_no`, and the planner produces multi-disc filenames.
    """
    from mutagen.flac import FLAC

    album_dir = tmp_path / "input" / "VA-Comp"
    album_dir.mkdir(parents=True)
    # 4 tracks per disc (≥3 required by the heuristic) across 2 discs.
    for disc in (1, 2):
        for n in range(1, 5):
            tn = disc * 100 + n  # 101, 102, 103, 104, 201, 202, 203, 204
            dst = album_dir / f"{tn:03d}_track.flac"
            shutil.copy2(silent_flac_template, dst)
            flac = FLAC(dst)
            flac["TITLE"] = f"Track {n}"
            flac["ARTIST"] = f"Artist {disc}-{n}"
            flac["ALBUM"] = "Compilation Vol 1"
            flac["DATE"] = "2003"
            flac["TRACKNUMBER"] = str(tn)
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
    out_album = out_root / "Various Artists" / "2003 - Compilation Vol 1"
    files = sorted(p.name for p in out_album.glob("*.m4a"))
    # Multi-disc prefix kicked in: `01-01 ... 01-04, 02-01 ... 02-04`.
    assert all(name.startswith(("01-0", "02-0")) for name in files), files
    assert any(name.startswith("01-01 -") for name in files)
    assert any(name.startswith("02-04 -") for name in files)


def test_scene_encoded_heuristic_does_not_fire_on_legit_99plus_album(
    silent_flac_template: Path, tmp_path: Path
) -> None:
    """A legit 100-track album (1..100, including a single 100) shouldn't be split.

    Only one track has `track_no >= 100` so the cluster `{1: 1}` doesn't have
    ≥2 disc prefixes — heuristic correctly bails.
    """
    from mutagen.flac import FLAC

    album_dir = tmp_path / "input" / "BigAlbum"
    album_dir.mkdir(parents=True)
    # Make 5 tracks: 1, 2, 3, 4, 100. Track 100 alone shouldn't trigger split.
    for tn in (1, 2, 3, 4, 100):
        dst = album_dir / f"{tn:03d}_track.flac"
        shutil.copy2(silent_flac_template, dst)
        flac = FLAC(dst)
        flac["TITLE"] = f"Track {tn}"
        flac["ARTIST"] = "Solo"
        flac["ALBUMARTIST"] = "Solo"
        flac["ALBUM"] = "Big Album"
        flac["DATE"] = "2024"
        flac["TRACKNUMBER"] = str(tn)
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
    out_album = out_root / "Solo" / "2024 - Big Album"
    files = sorted(p.name for p in out_album.glob("*.m4a"))
    # Single-disc — heuristic didn't split. No `01-` / `02-` disc-prefixed names.
    assert not any(name.startswith(("01-", "02-")) for name in files), files
    # Track 100 lands at "100 - …" (the {:02d} format is min-width and accommodates).
    assert any(name.startswith("100 -") for name in files)


def test_collision_disambiguation_keeps_every_track(silent_flac_template: Path, tmp_path: Path) -> None:
    """Two tracks sharing track-no + title must both end up on disk, not silently dropped.

    Distinct durations let dedup correctly classify them as remix-vs-original
    (keep both via collision rename) rather than rip-group dups (drop second).
    """
    from mutagen.flac import FLAC

    from tests.conftest import make_silent_flac

    album_dir = tmp_path / "input" / "DupAlbum"
    album_dir.mkdir(parents=True)
    for src_name, duration in [("a.flac", 0.2), ("b.flac", 1.0)]:
        dst = album_dir / src_name
        make_silent_flac(dst, duration=duration)
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

    from tests.conftest import make_silent_flac

    album_dir = tmp_path / "input" / "DupAlbum"
    album_dir.mkdir(parents=True)
    # Distinct durations → distinct sizes → dedup keeps all three.
    titles_with_durations = [("Same", 0.2), ("Same", 1.0), ("Same (2)", 1.5)]
    for i, (title, duration) in enumerate(titles_with_durations, start=1):
        dst = album_dir / f"src{i}.flac"
        make_silent_flac(dst, duration=duration)
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


def test_dedupe_drops_duplicate_tracks_with_same_disc_track_title_artist(tmp_path: Path) -> None:
    """Same tags AND ~same audio duration → same content shipped twice → drop the second."""
    from musickit.metadata import SourceTrack
    from musickit.pipeline import _dedupe_duplicate_tracks

    a = tmp_path / "01. Artist - Title.flac"
    b = tmp_path / "01 Title.flac"
    c = tmp_path / "02. Artist - Other.flac"
    a.touch()
    b.touch()
    c.touch()

    tracks = [
        SourceTrack(path=a, title="Title", artist="Artist", track_no=1, disc_no=1, duration_s=180.5),
        SourceTrack(path=b, title="Title", artist="Artist", track_no=1, disc_no=1, duration_s=180.6),
        SourceTrack(path=c, title="Other", artist="Artist", track_no=2, disc_no=1, duration_s=210.0),
    ]
    warnings: list[str] = []
    deduped = _dedupe_duplicate_tracks(tracks, warnings)
    assert len(deduped) == 2
    assert [t.path.name for t in deduped] == ["01. Artist - Title.flac", "02. Artist - Other.flac"]
    assert any("dropped duplicate" in w for w in warnings)


def test_dedupe_keeps_same_tag_distinct_duration(tmp_path: Path) -> None:
    """Same tags but DIFFERENT audio duration → keep both (remix-vs-original at same track_no)."""
    from musickit.metadata import SourceTrack
    from musickit.pipeline import _dedupe_duplicate_tracks

    a = tmp_path / "a.flac"
    b = tmp_path / "b.flac"
    a.touch()
    b.touch()

    tracks = [
        SourceTrack(path=a, title="Same", artist="A", track_no=1, disc_no=1, duration_s=180.0),
        SourceTrack(path=b, title="Same", artist="A", track_no=1, disc_no=1, duration_s=240.0),
    ]
    warnings: list[str] = []
    deduped = _dedupe_duplicate_tracks(tracks, warnings)
    assert len(deduped) == 2
    assert warnings == []


def test_dedupe_keeps_both_when_duration_unknown(tmp_path: Path) -> None:
    """If we can't read duration on either side, prefer keep-both (collision-rename handles it)."""
    from musickit.metadata import SourceTrack
    from musickit.pipeline import _dedupe_duplicate_tracks

    tracks = [
        SourceTrack(path=tmp_path / "a.flac", title="X", artist="A", track_no=1),
        SourceTrack(path=tmp_path / "b.flac", title="X", artist="A", track_no=1),
    ]
    deduped = _dedupe_duplicate_tracks(tracks, [])
    assert len(deduped) == 2


def test_dedupe_keeps_distinct_tracks(tmp_path: Path) -> None:
    """Different titles or different track numbers are kept as-is."""
    from pathlib import Path as PathType

    from musickit.metadata import SourceTrack
    from musickit.pipeline import _dedupe_duplicate_tracks

    tracks = [
        SourceTrack(path=PathType("/01.flac"), title="Track 1", artist="A", track_no=1),
        SourceTrack(path=PathType("/02.flac"), title="Track 2", artist="A", track_no=2),
        SourceTrack(path=PathType("/03.flac"), title="Track 1", artist="B", track_no=1),
    ]
    warnings: list[str] = []
    deduped = _dedupe_duplicate_tracks(tracks, warnings)
    assert len(deduped) == 3
    assert warnings == []


def test_cover_discovery_matches_token_keywords(tmp_path: Path) -> None:
    """Scene-rip covers like `*-(front)-*.jpg` should be picked up.

    Real-world filenames don't stick to `cover.jpg`/`folder.jpg` — Absolute
    Music rips ship `absolute music 45 front.jpg`,
    `000_va_-_absolute_music_47_(swedish_edition)-2cd-2004-(front)-dqm.jpg`.
    """
    from io import BytesIO

    from PIL import Image

    from musickit import cover as cover_mod

    album_dir = tmp_path / "scene-rip"
    album_dir.mkdir()
    img = Image.new("RGB", (600, 600), color="red")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    payload = buf.getvalue()
    front = album_dir / "000_va_-_absolute_music_47_(swedish_edition)-2cd-2004-(front)-dqm.jpg"
    front.write_bytes(payload)
    back = album_dir / "000_va_-_absolute_music_47_(swedish_edition)-2cd-2004-(back)-dqm.jpg"
    back.write_bytes(payload)
    other = album_dir / "absolute music 45 front.jpg"
    other.write_bytes(payload)
    unrelated = album_dir / "frontiers_was_a_journey_album.jpg"  # `front` lookalike
    unrelated.write_bytes(payload)

    matches = cover_mod._find_folder_images(album_dir)
    names = {p.name for p in matches}
    assert front.name in names
    assert other.name in names
    # `back` cover is filtered out (we don't want a back cover when no front exists).
    assert back.name not in names
    # `frontiers` should not match the `front` token (negative-lookahead works).
    assert unrelated.name not in names
