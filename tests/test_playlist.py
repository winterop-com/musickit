"""Auto-generated playlists — similarity, builder, M3U I/O, and CLI."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from musickit import library as library_mod
from musickit import playlist as playlist_mod
from musickit.cli import app
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.playlist.io import read_m3u8, write_m3u8
from musickit.playlist.similarity import _genre_tokens, _year_int, score
from tests.test_library import _make_track

# ---------------------------------------------------------------------------
# similarity.score
# ---------------------------------------------------------------------------


def _track(
    *,
    title: str = "T",
    artist: str | None = "A",
    album_artist: str | None = None,
    album: str | None = "Album",
    year: str | None = "2020",
    genre: str | None = None,
    genres: list[str] | None = None,
    duration_s: float = 180.0,
    path: Path | None = None,
) -> LibraryTrack:
    """Construct a minimal LibraryTrack for similarity tests."""
    return LibraryTrack(
        path=path or Path(f"/tmp/{title}.m4a"),
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        year=year,
        genre=genre,
        genres=genres or [],
        duration_s=duration_s,
    )


def test_score_self_is_negative_infinity() -> None:
    """A track scored against itself short-circuits to -inf so it never gets picked."""
    t = _track()
    assert score(t, t) == float("-inf")


def test_score_artist_match_dominates() -> None:
    """Same album_artist beats genre+year matches put together."""
    seed = _track(title="A", artist="X", album_artist="X", year="2020", genre="rock")
    same_artist_no_genre = _track(
        title="B", artist="X", album_artist="X", year="2010", genre="ambient", path=Path("/tmp/b.m4a")
    )
    same_genre_diff_artist = _track(
        title="C", artist="Z", album_artist="Z", year="2020", genre="rock", path=Path("/tmp/c.m4a")
    )
    assert score(seed, same_artist_no_genre) > score(seed, same_genre_diff_artist)


def test_score_genre_token_overlap_counts() -> None:
    """Token-set overlap on genre yields the genre bonus.

    Use different artists so the genre signal is the differentiator —
    otherwise both candidates inherit the artist-match bonus and tie.
    """
    seed = _track(artist="A", genre="Indie Rock / Alternative")
    overlap = _track(artist="B", genre="Alternative Rock", path=Path("/tmp/c.m4a"))
    no_overlap = _track(artist="C", genre="Country", path=Path("/tmp/d.m4a"))
    assert score(seed, overlap) > score(seed, no_overlap)


def test_score_year_proximity_scales() -> None:
    """Same year > within 5y > within 15y > else."""
    seed = _track(year="2020")
    same = score(seed, _track(year="2020", path=Path("/tmp/a.m4a")))
    near = score(seed, _track(year="2018", path=Path("/tmp/b.m4a")))
    mid = score(seed, _track(year="2010", path=Path("/tmp/c.m4a")))
    far = score(seed, _track(year="1985", path=Path("/tmp/d.m4a")))
    assert same > near > mid > far


def test_score_compilation_mismatch_penalised() -> None:
    """Various Artists vs. mainline album_artist gets the penalty."""
    seed = _track(album_artist="ArtistA", artist="ArtistA")
    va_track = _track(album_artist="Various Artists", artist="ArtistA", path=Path("/tmp/v.m4a"))
    mainline = _track(album_artist="ArtistA", artist="ArtistA", path=Path("/tmp/m.m4a"))
    assert score(seed, va_track) < score(seed, mainline)


def test_genre_tokens_splits_on_separators_and_whitespace() -> None:
    """Splits on `/ , ; & -` and whitespace; lowercases."""
    t = _track(genre="Indie Rock / Alternative", genres=["Pop & Soul", "rock"])
    tokens = _genre_tokens(t)
    # Whitespace split: "Indie Rock" -> {"indie", "rock"}.
    assert "indie" in tokens
    assert "rock" in tokens
    assert "alternative" in tokens
    assert "pop" in tokens
    assert "soul" in tokens


def test_year_int_extracts_first_4_digits() -> None:
    assert _year_int(_track(year="2020-05-12")) == 2020
    assert _year_int(_track(year="1999")) == 1999
    assert _year_int(_track(year="abc")) is None
    assert _year_int(_track(year=None)) is None


# ---------------------------------------------------------------------------
# build.generate
# ---------------------------------------------------------------------------


def _build_index(*albums: LibraryAlbum) -> LibraryIndex:
    return LibraryIndex(root=Path("/tmp/lib"), albums=list(albums))


def _make_album(
    artist: str,
    album: str,
    year: str,
    genre: str,
    *,
    n_tracks: int = 3,
) -> LibraryAlbum:
    """Build a LibraryAlbum with `n_tracks` synthetic tracks."""
    tracks = [
        LibraryTrack(
            path=Path(f"/tmp/lib/{artist}/{album}/{i:02d}.m4a"),
            title=f"T{i}",
            artist=artist,
            album_artist=artist,
            album=album,
            year=year,
            genre=genre,
            duration_s=200.0,
        )
        for i in range(1, n_tracks + 1)
    ]
    return LibraryAlbum(
        path=Path(f"/tmp/lib/{artist}/{album}"),
        artist_dir=artist,
        album_dir=album,
        tag_album=album,
        tag_year=year,
        tag_album_artist=artist,
        tag_genre=genre,
        track_count=n_tracks,
        tracks=tracks,
    )


def test_generate_anchors_to_seed_artist() -> None:
    """Tracks by the same artist appear near the top of the result."""
    a1 = _make_album("Pixies", "Doolittle", "1989", "alternative", n_tracks=3)
    a2 = _make_album("Pixies", "Bossanova", "1990", "alternative", n_tracks=3)
    far = _make_album("Mozart", "Symphonies", "1788", "classical", n_tracks=3)
    idx = _build_index(a1, a2, far)

    # Per-artist cap is 4; with 6 Pixies tracks total in the index (minus
    # the seed itself) and 3 Mozart tracks, expected pick order:
    # seed + 3 more Pixies (cap=4 satisfied) + Mozart only after.
    seed = a1.tracks[0]
    result = playlist_mod.generate(idx, seed, target_minutes=60.0, random_seed=42)

    # Seed first.
    assert result.tracks[0].path == seed.path
    # Remaining Pixies tracks should appear before any Mozart.
    pixies_after = [t for t in result.tracks[1:] if t.artist == "Pixies"]
    mozart_after = [t for t in result.tracks[1:] if t.artist == "Mozart"]
    pixies_idxs = [i for i, t in enumerate(result.tracks) if t.artist == "Pixies"]
    mozart_idxs = [i for i, t in enumerate(result.tracks) if t.artist == "Mozart"]
    assert pixies_after, "expected at least one more Pixies pick"
    if mozart_idxs:
        # All Pixies tracks come before all Mozart tracks.
        assert max(pixies_idxs) < min(mozart_idxs)
    # And the per-artist cap caps Pixies at 4 (seed + 3).
    assert len(pixies_after) <= 3, "per-artist cap=4 means at most 3 picks beyond the seed"
    _ = mozart_after


def test_generate_respects_per_album_cap() -> None:
    """No more than 2 picks from any one album."""
    a = _make_album("Solo", "Big", "2020", "indie", n_tracks=8)
    idx = _build_index(a)
    seed = a.tracks[0]
    result = playlist_mod.generate(idx, seed, target_minutes=60.0, random_seed=1)
    # Same album as the seed: at most 2 picks total = seed + 1 more.
    same_album = [t for t in result.tracks if t.album == "Big"]
    assert len(same_album) <= 2


def test_generate_resolves_seed_by_basename() -> None:
    """Seed string can be a bare filename, not just an absolute path."""
    a = _make_album("X", "Y", "2020", "rock", n_tracks=3)
    idx = _build_index(a)
    result = playlist_mod.generate(idx, "01.m4a", target_minutes=10.0)
    assert result.tracks[0].path.name == "01.m4a"


def test_generate_unknown_seed_raises() -> None:
    a = _make_album("X", "Y", "2020", "rock")
    idx = _build_index(a)
    try:
        playlist_mod.generate(idx, "/does/not/exist.m4a", target_minutes=10.0)
    except ValueError as e:
        assert "seed not found" in str(e)
    else:
        msg = "expected ValueError for unknown seed"
        raise AssertionError(msg)


def test_generate_target_duration_approximately_met() -> None:
    """Actual duration is at least one track short of, then meets-or-exceeds, target."""
    # Build a deep enough pool so the target is reachable.
    a = _make_album("X", "Y", "2020", "rock", n_tracks=8)
    b = _make_album("Z", "W", "2020", "rock", n_tracks=8)
    idx = _build_index(a, b)
    seed = a.tracks[0]
    result = playlist_mod.generate(idx, seed, target_minutes=10.0)  # 600s
    # Last picked track may push over the target; that's expected.
    assert result.actual_seconds >= result.target_seconds - 200


def test_generate_default_name_uses_seed_metadata() -> None:
    a = _make_album("Pixies", "Doolittle", "1989", "rock", n_tracks=3)
    idx = _build_index(a)
    seed = a.tracks[0]
    result = playlist_mod.generate(idx, seed, target_minutes=5.0)
    assert "Pixies" in result.name
    assert seed.title is not None
    assert seed.title in result.name


# ---------------------------------------------------------------------------
# io.write_m3u8 / read_m3u8 round-trip
# ---------------------------------------------------------------------------


def test_m3u8_round_trip(tmp_path: Path) -> None:
    a = _make_album("X", "Y", "2020", "rock", n_tracks=3)
    idx = _build_index(a)
    seed = a.tracks[0]
    result = playlist_mod.generate(idx, seed, target_minutes=5.0)

    out = tmp_path / "mix.m3u8"
    written = write_m3u8(result, out)
    assert written == out
    text = out.read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n")
    assert f"#PLAYLIST:{result.name}" in text

    # read_m3u8 returns the path entries in order.
    paths = read_m3u8(out)
    assert len(paths) == len(result.tracks)


def test_write_m3u8_uses_relative_paths_when_under_base(tmp_path: Path) -> None:
    """Tracks under the playlist's parent dir are written relative."""
    base = tmp_path / "lib"
    track_path = base / "Artist" / "Album" / "01.m4a"
    track_path.parent.mkdir(parents=True)
    track_path.touch()

    track = LibraryTrack(path=track_path, title="T", artist="A", duration_s=180)
    result = playlist_mod.PlaylistResult(tracks=[track], name="Mix", target_seconds=600, actual_seconds=180)
    out = base / ".musickit" / "playlists" / "mix.m3u8"
    write_m3u8(result, out)
    body = out.read_text(encoding="utf-8")
    # Relative path from <base>/.musickit/playlists to <base>/Artist/Album/01.m4a
    # is `../../Artist/Album/01.m4a`.
    assert "../../Artist/Album/01.m4a" in body
    assert str(track_path) not in body, "absolute path should NOT appear when relative is possible"


def test_write_m3u8_uses_walk_up_for_cross_tree_paths(tmp_path: Path) -> None:
    """Tracks in sibling subtrees get a `..`-prefixed relative path."""
    out_dir = tmp_path / "playlists"
    track_dir = tmp_path / "elsewhere"
    track_dir.mkdir()
    track_path = track_dir / "01.m4a"
    track_path.touch()

    track = LibraryTrack(path=track_path, title="T", artist="A", duration_s=180)
    result = playlist_mod.PlaylistResult(tracks=[track], name="Mix", target_seconds=600, actual_seconds=180)
    out = out_dir / "mix.m3u8"
    write_m3u8(result, out)
    body = out.read_text(encoding="utf-8")
    # Cross-tree path computed via `walk_up=True`.
    assert "../elsewhere/01.m4a" in body
    # Absolute path should NOT appear when a relative one is computable.
    assert str(track_path.resolve()) not in body


# ---------------------------------------------------------------------------
# CLI: musickit playlist gen / list / show
# ---------------------------------------------------------------------------


def _stage_min_library(tmp_path: Path, silent_flac_template: Path) -> Path:
    """Build a 2-album library so `gen` has a candidate pool."""
    root = tmp_path / "lib"
    a1 = root / "Pixies" / "1989 - Doolittle"
    a2 = root / "Pixies" / "1990 - Bossanova"
    _make_track(a1, silent_flac_template, filename="01 - Debaser.m4a", title="Debaser", artist="Pixies")
    _make_track(a1, silent_flac_template, filename="02 - Tame.m4a", title="Tame", artist="Pixies", track_no=2)
    _make_track(a2, silent_flac_template, filename="01 - Cecilia Ann.m4a", title="Cecilia Ann", artist="Pixies")
    return root


def test_cli_gen_produces_m3u8(silent_flac_template: Path, tmp_path: Path) -> None:
    """`musickit playlist gen` writes a .m3u8 with the seed at the top."""
    root = _stage_min_library(tmp_path, silent_flac_template)
    seed = root / "Pixies" / "1989 - Doolittle" / "01 - Debaser.m4a"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "playlist",
            "gen",
            str(root),
            "--seed",
            str(seed),
            "--minutes",
            "5",
            "--random-seed",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output

    pdir = root / library_mod.INDEX_DIR_NAME / "playlists"
    files = list(pdir.glob("*.m3u8"))
    assert len(files) == 1, f"expected one playlist file, got {files}"
    body = files[0].read_text(encoding="utf-8")
    assert "Debaser" in body, "seed track's title should appear in the EXTINF line"


def test_cli_gen_with_unknown_seed_exits_1(silent_flac_template: Path, tmp_path: Path) -> None:
    root = _stage_min_library(tmp_path, silent_flac_template)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "playlist",
            "gen",
            str(root),
            "--seed",
            "/does/not/exist.m4a",
            "--minutes",
            "5",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "seed not found" in result.output


def test_cli_list_empty(silent_flac_template: Path, tmp_path: Path) -> None:
    """With no playlists generated yet, list reports the empty state."""
    root = _stage_min_library(tmp_path, silent_flac_template)
    runner = CliRunner()
    result = runner.invoke(app, ["playlist", "list", str(root)])
    assert result.exit_code == 0, result.output
    assert "No playlists" in result.output


def test_cli_list_after_gen(silent_flac_template: Path, tmp_path: Path) -> None:
    """`list` enumerates files written by `gen`."""
    root = _stage_min_library(tmp_path, silent_flac_template)
    seed = root / "Pixies" / "1989 - Doolittle" / "01 - Debaser.m4a"
    runner = CliRunner()
    runner.invoke(
        app,
        ["playlist", "gen", str(root), "--seed", str(seed), "--minutes", "5", "--name", "MyMix"],
    )
    result = runner.invoke(app, ["playlist", "list", str(root)])
    assert result.exit_code == 0, result.output
    assert "mymix" in result.output.lower()


def test_cli_show_known_playlist(silent_flac_template: Path, tmp_path: Path) -> None:
    """`show` prints the resolved track paths."""
    root = _stage_min_library(tmp_path, silent_flac_template)
    seed = root / "Pixies" / "1989 - Doolittle" / "01 - Debaser.m4a"
    runner = CliRunner()
    runner.invoke(
        app,
        ["playlist", "gen", str(root), "--seed", str(seed), "--minutes", "5", "--name", "MyMix"],
    )
    result = runner.invoke(app, ["playlist", "show", str(root), "mymix"])
    assert result.exit_code == 0, result.output
    assert "Debaser" in result.output


def test_cli_show_unknown_playlist_exits_1(silent_flac_template: Path, tmp_path: Path) -> None:
    root = _stage_min_library(tmp_path, silent_flac_template)
    runner = CliRunner()
    result = runner.invoke(app, ["playlist", "show", str(root), "no-such-mix"])
    assert result.exit_code == 1, result.output
