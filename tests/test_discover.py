"""Album discovery."""

from pathlib import Path

from musickit.discover import discover_albums


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_discover_groups_one_album_per_leaf_dir(tmp_path: Path) -> None:
    _touch(tmp_path / "Artist A" / "Album 1" / "01 - one.flac")
    _touch(tmp_path / "Artist A" / "Album 1" / "02 - two.flac")
    _touch(tmp_path / "Artist A" / "Album 1" / "folder.jpg")  # not audio
    _touch(tmp_path / "Artist B" / "Album 2" / "01 - one.mp3")

    albums = discover_albums(tmp_path)

    assert [a.path.name for a in albums] == ["Album 1", "Album 2"]
    assert [len(a.tracks) for a in albums] == [2, 1]


def test_discover_skips_wrapper_dirs_with_no_audio(tmp_path: Path) -> None:
    # Wrapper dir with only sub-folders containing audio.
    _touch(tmp_path / "Discography" / "2012 - Album/01.flac")
    albums = discover_albums(tmp_path)
    # Wrapper folder does not appear; only the leaf does.
    assert [a.path.name for a in albums] == ["2012 - Album"]


def test_discover_returns_empty_for_missing_root(tmp_path: Path) -> None:
    assert discover_albums(tmp_path / "does-not-exist") == []


def test_discover_ignores_dotfiles_and_unsupported_extensions(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Album"
    _touch(album / "01.flac")
    _touch(album / ".DS_Store")
    _touch(album / "notes.txt")
    albums = discover_albums(tmp_path)
    assert [t.name for t in albums[0].tracks] == ["01.flac"]


def test_discover_merges_disc_subfolders(tmp_path: Path) -> None:
    album = tmp_path / "ACDC - Greatest Hits"
    _touch(album / "folder.jpg")
    _touch(album / "CD1" / "01 - One.flac")
    _touch(album / "CD1" / "02 - Two.flac")
    _touch(album / "CD2" / "01 - Three.flac")
    _touch(album / "CD2" / "02 - Four.flac")

    albums = discover_albums(tmp_path)

    assert len(albums) == 1
    merged = albums[0]
    assert merged.path == album  # anchored at parent so folder.jpg is reachable
    assert [t.name for t in merged.tracks] == ["01 - One.flac", "02 - Two.flac", "01 - Three.flac", "02 - Four.flac"]
    assert merged.disc_total == 2
    assert merged.disc_of(album / "CD1" / "01 - One.flac") == 1
    assert merged.disc_of(album / "CD2" / "02 - Four.flac") == 2


def test_discover_merge_handles_label_variants(tmp_path: Path) -> None:
    for variant in ("CD 1", "Disc 2", "Disk3"):
        _touch(tmp_path / "Album" / variant / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 1
    assert albums[0].disc_total == 3


def test_discover_does_not_merge_when_siblings_are_mixed(tmp_path: Path) -> None:
    # Parent has both a disc folder AND a non-disc audio folder — keep them separate.
    _touch(tmp_path / "Parent" / "CD1" / "01.flac")
    _touch(tmp_path / "Parent" / "Bonus" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 2
    assert {a.path.name for a in albums} == {"CD1", "Bonus"}


def test_discover_merges_disc_indicator_in_long_folder_names(tmp_path: Path) -> None:
    # Layout from the VA Dance Hits rip: `<Album Name> (CD1)` / `<...> (CD2)`.
    parent = tmp_path / "VA - Best of Dance Hits (1998)[FLAC]"
    _touch(parent / "VA - Best Of Dance Hits (CD1)" / "01.flac")
    _touch(parent / "VA - Best Of Dance Hits (CD2)" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 1
    # Anchor = first disc subfolder so we have a real path with the album
    # name baked in (`... (CD1)`); discover passes this through and the
    # pipeline strips the trailing `(CDx)` for the final folder name.
    assert albums[0].path.name == "VA - Best Of Dance Hits (CD1)"
    assert albums[0].disc_total == 2


def test_discover_does_not_merge_disc_suffix_when_prefixes_differ(tmp_path: Path) -> None:
    # If the prefixes differ, these are two separate albums that happen to
    # both have a disc indicator — keep them apart.
    parent = tmp_path / "Parent"
    _touch(parent / "Album A (CD1)" / "01.flac")
    _touch(parent / "Album B (CD1)" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 2


def test_discover_handles_cd_dash_separator(tmp_path: Path) -> None:
    parent = tmp_path / "Armin (2025)"
    _touch(parent / "CD-1" / "01.flac")
    _touch(parent / "CD-2" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 1
    assert albums[0].disc_total == 2


def test_discover_merges_disc_with_trailing_text(tmp_path: Path) -> None:
    # Cranberries layout: bonus disc has extra description after the disc number.
    parent = tmp_path / "Limited Edition"
    _touch(parent / "CD1" / "01.flac")
    _touch(parent / "CD2 (Bonus Live CD)" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 1
    assert albums[0].disc_total == 2


def test_discover_merges_only_matching_prefixes_under_mixed_parent(tmp_path: Path) -> None:
    # Ultimate Queen layout: many bare albums + a few `... (Disc 1)`/`(Disc 2)` pairs.
    root = tmp_path / "Queen Set"
    _touch(root / "Queen - A Night At The Opera" / "01.flac")
    _touch(root / "Queen - Hot Space" / "01.flac")
    _touch(root / "Queen - Live At Wembley '86 (Disc 1)" / "01.flac")
    _touch(root / "Queen - Live At Wembley '86 (Disc 2)" / "01.flac")
    _touch(root / "Queen - Live Killers (Disc 1)" / "01.flac")
    _touch(root / "Queen - Live Killers (Disc 2)" / "01.flac")
    albums = discover_albums(tmp_path)
    # 2 standalone + 2 merged = 4 albums.
    assert len(albums) == 4
    discs_by_album = {a.path.name: a.disc_total for a in albums}
    assert discs_by_album["Queen - A Night At The Opera"] is None
    # Merged albums anchor at first disc dir (...(Disc 1)).
    assert any(a.disc_total == 2 and "Wembley" in a.path.name for a in albums)
    assert any(a.disc_total == 2 and "Live Killers" in a.path.name for a in albums)


def test_discover_does_not_merge_singleton_disc_dirs_under_separate_parents(tmp_path: Path) -> None:
    # Two unrelated albums each rip with only `CD1` inside (no CD2 sibling).
    # They must NOT merge with each other across parent dirs — each becomes
    # a single-disc album that just happens to live under a `CD1` folder.
    _touch(tmp_path / "Album A" / "CD1" / "01.flac")
    _touch(tmp_path / "Album B" / "CD1" / "01.flac")
    albums = discover_albums(tmp_path)
    assert len(albums) == 2
    parents = {a.path.parent.name for a in albums}
    assert parents == {"Album A", "Album B"}
    # Singletons aren't promoted to multi-disc, even though their name matches.
    assert all(a.disc_total is None for a in albums)


def test_discover_drops_disc_subfolders_when_parent_owns_audio(tmp_path: Path) -> None:
    # Layout we hit on a SOAD discography rip: parent has the studio tracks at
    # top level AND duplicate Disc 1 / Disc 2 subfolders. Don't double-emit.
    album = tmp_path / "Album"
    _touch(album / "01.flac")
    _touch(album / "02.flac")
    _touch(album / "Disc 1" / "01.flac")
    _touch(album / "Disc 2" / "01.flac")

    albums = discover_albums(tmp_path)

    assert len(albums) == 1
    assert albums[0].path == album
    assert [t.name for t in albums[0].tracks] == ["01.flac", "02.flac"]
    assert albums[0].disc_for_track == {}
    assert albums[0].disc_total is None


def test_discover_does_not_merge_box_set_with_distinct_album_names(silent_flac_template: Path, tmp_path: Path) -> None:
    """3CD box set where each disc has its OWN album tag → keep as 3 albums.

    Real Queen case: ``Greatest Hits I, II & III [3CD Box Set]`` has three
    CD subfolders, each with a different ``TALB`` (``Greatest Hits I``,
    ``... II``, ``... III``). The discover layer used to merge them into
    one multi-disc album (losing per-CD identity); now it inspects the
    first track of each disc and skips the merge when names differ.
    """
    import shutil

    from mutagen.flac import FLAC

    box = tmp_path / "Queen - Box Set"
    for cd_idx, name in enumerate(["Hits I", "Hits II", "Hits III"], start=1):
        d = box / f"CD{cd_idx} - {name}"
        d.mkdir(parents=True)
        track = d / "01.flac"
        shutil.copy2(silent_flac_template, track)
        flac = FLAC(track)
        flac["ALBUM"] = name
        flac["ARTIST"] = "Queen"
        flac.save()

    albums = discover_albums(tmp_path)
    # Three separate albums, not one merged multi-disc album.
    assert len(albums) == 3
    assert sorted(a.path.name for a in albums) == [
        "CD1 - Hits I",
        "CD2 - Hits II",
        "CD3 - Hits III",
    ]
    # None of them is flagged as multi-disc.
    assert all(a.disc_total is None for a in albums)


def test_discover_still_merges_when_disc_names_match(silent_flac_template: Path, tmp_path: Path) -> None:
    """Real multi-disc album where all discs share the same TALB → merge as before."""
    import shutil

    from mutagen.flac import FLAC

    album = tmp_path / "Pink Floyd - The Wall"
    for cd_idx in (1, 2):
        d = album / f"CD{cd_idx}"
        d.mkdir(parents=True)
        track = d / "01.flac"
        shutil.copy2(silent_flac_template, track)
        flac = FLAC(track)
        # Both discs share the album name (the Wall ships with TALB=
        # "The Wall" on both discs, optionally with " (Disc 1)" suffix
        # which clean_album_title strips).
        flac["ALBUM"] = "The Wall (Disc 1)" if cd_idx == 1 else "The Wall (Disc 2)"
        flac["ARTIST"] = "Pink Floyd"
        flac.save()

    albums = discover_albums(tmp_path)
    assert len(albums) == 1
    assert albums[0].disc_total == 2
