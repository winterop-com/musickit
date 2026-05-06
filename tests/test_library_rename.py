"""rename_album_to_match_tags + compute_new_album_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from musickit.library import (
    LibraryAlbum,
    LibraryTrack,
    RenameError,
    compute_new_album_path,
    rename_album_to_match_tags,
)


def _album(
    root: Path, artist: str, dir_name: str, *, tag_album: str, tag_year: str | None, tag_artist: str
) -> LibraryAlbum:
    album_dir = root / artist / dir_name
    album_dir.mkdir(parents=True)
    track_path = album_dir / "01 - T.flac"
    track_path.write_bytes(b"")
    track = LibraryTrack(path=track_path, title="T", artist=tag_artist, album=tag_album)
    return LibraryAlbum(
        path=album_dir,
        artist_dir=artist,
        album_dir=dir_name,
        tag_album=tag_album,
        tag_year=tag_year,
        tag_album_artist=tag_artist,
        track_count=1,
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# compute_new_album_path
# ---------------------------------------------------------------------------


def test_compute_path_album_only(tmp_path: Path) -> None:
    album = _album(tmp_path, "ABBA", "Old Name", tag_album="Arrival", tag_year="1976", tag_artist="ABBA")
    expected = tmp_path / "ABBA" / "1976 - Arrival"
    assert compute_new_album_path(album, tmp_path) == expected


def test_compute_path_with_album_artist_change(tmp_path: Path) -> None:
    album = _album(tmp_path, "ABBA", "Arrival", tag_album="Arrival", tag_year="1976", tag_artist="ABBA & Friends")
    expected = tmp_path / "ABBA & Friends" / "1976 - Arrival"
    assert compute_new_album_path(album, tmp_path) == expected


def test_compute_path_no_year(tmp_path: Path) -> None:
    album = _album(tmp_path, "Artist", "Album", tag_album="Album", tag_year=None, tag_artist="Artist")
    expected = tmp_path / "Artist" / "Album"
    assert compute_new_album_path(album, tmp_path) == expected


def test_compute_path_returns_existing_when_album_tag_missing(tmp_path: Path) -> None:
    album_dir = tmp_path / "Artist" / "Strange Folder"
    album_dir.mkdir(parents=True)
    track = LibraryTrack(path=album_dir / "01.flac", title="T")
    track.path.write_bytes(b"")
    album = LibraryAlbum(
        path=album_dir,
        artist_dir="Artist",
        album_dir="Strange Folder",
        tag_album=None,  # no album tag → no idea what to call it
        track_count=1,
        tracks=[track],
    )
    assert compute_new_album_path(album, tmp_path) == album_dir


# ---------------------------------------------------------------------------
# rename_album_to_match_tags
# ---------------------------------------------------------------------------


def test_rename_within_same_artist_dir(tmp_path: Path) -> None:
    album = _album(tmp_path, "ABBA", "Old Name", tag_album="Arrival", tag_year="1976", tag_artist="ABBA")
    track_path_before = album.tracks[0].path

    result = rename_album_to_match_tags(album, tmp_path)

    assert result.changed is True
    assert result.new_path == tmp_path / "ABBA" / "1976 - Arrival"
    assert result.new_path.exists()
    assert not result.old_path.exists()

    # In-memory paths updated.
    assert album.path == result.new_path
    assert album.album_dir == "1976 - Arrival"
    assert album.artist_dir == "ABBA"
    new_track_path = album.tracks[0].path
    assert new_track_path.parent == result.new_path
    assert new_track_path.name == track_path_before.name
    assert new_track_path.exists()


def test_rename_across_artist_dirs(tmp_path: Path) -> None:
    """Changing tag_album_artist moves the album under the new artist parent."""
    album = _album(
        tmp_path, "Daft Punk", "1997 - Homework", tag_album="Homework", tag_year="1997", tag_artist="Daft Punk Remix"
    )
    result = rename_album_to_match_tags(album, tmp_path)

    expected = tmp_path / "Daft Punk Remix" / "1997 - Homework"
    assert result.new_path == expected
    assert result.new_path.exists()
    assert album.artist_dir == "Daft Punk Remix"


def test_rename_noop_when_already_correct(tmp_path: Path) -> None:
    """Album dir already matches tags → no-op, no rename."""
    album = _album(tmp_path, "ABBA", "1976 - Arrival", tag_album="Arrival", tag_year="1976", tag_artist="ABBA")
    result = rename_album_to_match_tags(album, tmp_path)
    assert result.changed is False
    assert album.path.exists()


def test_rename_collision_raises(tmp_path: Path) -> None:
    """Target dir already exists → RenameError, no fs change."""
    album = _album(tmp_path, "ABBA", "Old", tag_album="Arrival", tag_year="1976", tag_artist="ABBA")
    # Pre-create the collision target.
    (tmp_path / "ABBA" / "1976 - Arrival").mkdir()

    with pytest.raises(RenameError, match="target already exists"):
        rename_album_to_match_tags(album, tmp_path)
    # Source is still where it was; in-memory state unchanged.
    assert album.path.exists()
    assert album.path.name == "Old"


def test_rename_creates_missing_artist_dir(tmp_path: Path) -> None:
    """Cross-artist rename creates the new artist parent dir if it doesn't exist."""
    album = _album(tmp_path, "OldArtist", "Album", tag_album="Album", tag_year="2020", tag_artist="NewArtist")
    assert not (tmp_path / "NewArtist").exists()

    result = rename_album_to_match_tags(album, tmp_path)
    assert result.new_path == tmp_path / "NewArtist" / "2020 - Album"
    assert result.new_path.exists()
    assert (tmp_path / "NewArtist").exists()


def test_rename_year_only_change(tmp_path: Path) -> None:
    """Just bumping the year (e.g. correcting 1975 → 1976) renames the dir."""
    album = _album(tmp_path, "ABBA", "1975 - Arrival", tag_album="Arrival", tag_year="1976", tag_artist="ABBA")
    result = rename_album_to_match_tags(album, tmp_path)
    assert result.changed is True
    assert result.new_path == tmp_path / "ABBA" / "1976 - Arrival"


def test_rename_track_outside_album_dir_left_alone(tmp_path: Path) -> None:
    """Tracks with paths outside the album dir (synthetic / Subsonic) keep their path."""
    album = _album(tmp_path, "Artist", "Album", tag_album="Album", tag_year="2020", tag_artist="Artist")
    # Inject a track whose path doesn't live under album.path — simulates
    # the synthetic /subsonic/... path used by client-mode albums.
    synthetic = LibraryTrack(path=Path("/subsonic/Artist/Album/02.flac"), title="T2")
    album.tracks.append(synthetic)
    album.track_count = 2

    rename_album_to_match_tags(album, tmp_path)
    # Synthetic track's path is unchanged; the rebase only touches tracks
    # whose path can be made relative to `old_path`.
    assert album.tracks[1].path == Path("/subsonic/Artist/Album/02.flac")
