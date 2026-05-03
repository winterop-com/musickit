"""Tag read + MP4 round-trip."""

from __future__ import annotations

from pathlib import Path

from mutagen.flac import FLAC
from mutagen.mp4 import MP4

from musickit import convert
from musickit.metadata import (
    AlbumSummary,
    MusicBrainzIds,
    SourceTrack,
    clean_album_title,
    read_source,
    summarize_album,
    write_mp4_tags,
)


def _tag_flac(path: Path, tags: dict[str, str]) -> None:
    flac = FLAC(path)
    if flac.tags is None:
        flac.add_tags()
    for key, value in tags.items():
        flac[key] = value
    flac.save()


def test_read_flac_picks_up_basic_tags(silent_flac: Path) -> None:
    _tag_flac(
        silent_flac,
        {
            "TITLE": "Radioactive",
            "ARTIST": "Imagine Dragons",
            "ALBUMARTIST": "Imagine Dragons",
            "ALBUM": "Night Visions",
            "DATE": "2012-09-04",
            "GENRE": "Alternative-Rock",
            "TRACKNUMBER": "01",
            "TRACKTOTAL": "11",
            "DISCNUMBER": "1",
            "DISCTOTAL": "1",
            "BPM": "136",
            "LABEL": "Interscope Records",
            "CATALOGNUMBER": "B0017324-02",
            "REPLAYGAIN_TRACK_GAIN": "-12.74 dB",
            "REPLAYGAIN_ALBUM_GAIN": "-11.34 dB",
        },
    )

    track = read_source(silent_flac)

    assert track.title == "Radioactive"
    assert track.artist == "Imagine Dragons"
    assert track.album_artist == "Imagine Dragons"
    assert track.album == "Night Visions"
    assert track.date == "2012-09-04"
    assert track.genre == "Alternative-Rock"
    assert (track.track_no, track.track_total) == (1, 11)
    assert (track.disc_no, track.disc_total) == (1, 1)
    assert track.bpm == 136
    assert track.label == "Interscope Records"
    assert track.catalog == "B0017324-02"
    assert track.replaygain["replaygain_track_gain"] == "-12.74 dB"
    assert track.replaygain["replaygain_album_gain"] == "-11.34 dB"


def test_summarize_album_majority_votes_and_detects_va() -> None:
    tracks = [
        SourceTrack(
            path=Path(f"/{i}.flac"),
            artist=artist,
            album_artist="VA",
            album="Hits",
            date="2020",
            track_no=i,
            track_total=3,
        )
        for i, artist in enumerate(["A", "B", "C"], start=1)
    ]
    summary = summarize_album(tracks)
    assert summary.album == "Hits"
    assert summary.album_artist == "VA"
    assert summary.is_compilation is True
    assert summary.year == "2020"
    assert summary.track_total == 3


def test_clean_album_title_strips_disc_suffixes() -> None:
    assert clean_album_title("Are You Ready: Best Of AC/DC [CD1]") == "Are You Ready: Best Of AC/DC"
    assert clean_album_title("Some Album [CD 2]") == "Some Album"
    assert clean_album_title("Some Album (Disc 1)") == "Some Album"
    assert clean_album_title("Some Album - Disk2") == "Some Album"
    # Embedded marker (Cranberries Roses layout).
    assert clean_album_title("Roses (CD2) Live In Madrid 12-03-2010") == "Roses Live In Madrid 12-03-2010"
    # Bare trailing `(N)` is treated as a disc index when the album would otherwise be intact.
    assert clean_album_title("Echoes - The Best Of Pink Floyd (1)") == "Echoes - The Best Of Pink Floyd"
    # Period as separator: `[CD.1]` / `(CD.2)` (Absolute Music Swedish-edition style).
    assert clean_album_title("Absolute Music 51 [CD.1]") == "Absolute Music 51"
    assert clean_album_title("Absolute Music 52 (CD.2)") == "Absolute Music 52"
    # Dots-as-separator + VA prefix (scene-rip vandalism: `VA.-.Absolute.Music.60`).
    assert clean_album_title("VA.-.Absolute.Music.60") == "Absolute Music 60"
    # Underscores-as-separator (folder-name fallback: `VA-Absolute_Music_45`).
    assert clean_album_title("Absolute_Music_45") == "Absolute Music 45"
    # Single-letter acronyms with periods are preserved.
    assert clean_album_title("R.E.M.") == "R.E.M."
    # Honorifics with trailing period + space are preserved.
    assert clean_album_title("St. Vincent") == "St. Vincent"
    assert clean_album_title("Mr. Big") == "Mr. Big"
    # Don't mangle albums with no suffix.
    assert clean_album_title("Night Visions") == "Night Visions"
    assert clean_album_title(None) is None


def test_summarize_album_strips_disc_suffix_from_album_title() -> None:
    tracks = [
        SourceTrack(path=Path("/cd1/01.flac"), album="Big Album [CD1]", disc_no=1),
        SourceTrack(path=Path("/cd2/01.flac"), album="Big Album [CD2]", disc_no=2),
    ]
    summary = summarize_album(tracks)
    assert summary.album == "Big Album"


def test_summarize_album_infers_compilation_when_album_artist_missing_and_artists_differ() -> None:
    tracks = [
        SourceTrack(path=Path("/1.flac"), artist="A", album="Mix", track_total=2),
        SourceTrack(path=Path("/2.flac"), artist="B", album="Mix", track_total=2),
    ]
    summary = summarize_album(tracks)
    assert summary.is_compilation is True


def test_summarize_album_album_quorum_rejects_stray_tagged_track() -> None:
    """A single misfiled track should not impersonate the album's whole-album tag.

    AM45 case: 40 tracks have no album tag, 1 stray says `Guilty`. Without the
    quorum rule, `Guilty` won the vote and became the album name even though
    the rest of the album was nothing of the sort.
    """
    tracks: list[SourceTrack] = []
    for i in range(40):
        tracks.append(SourceTrack(path=Path(f"/{i}.mp3"), album=None, artist="Various"))
    tracks.append(SourceTrack(path=Path("/stray1.mp3"), album="Guilty", artist="Blue"))
    tracks.append(SourceTrack(path=Path("/stray2.mp3"), album="Seal IV", artist="Seal"))
    summary = summarize_album(tracks)
    # Neither stray tag has the >50% quorum, so summary.album falls back to None.
    # Pipeline-level dirname fallback then takes over.
    assert summary.album is None


def test_summarize_album_album_quorum_accepts_unanimous_tags() -> None:
    tracks = [SourceTrack(path=Path(f"/{i}.flac"), album="Night Visions") for i in range(11)]
    summary = summarize_album(tracks)
    assert summary.album == "Night Visions"


def test_summarize_album_detects_compilation_when_per_track_artist_is_va_marker() -> None:
    # Rip stamps every track with ARTIST=VA and leaves album_artist blank.
    tracks = [SourceTrack(path=Path(f"/{i}.flac"), artist="VA", album="Mix") for i in range(3)]
    summary = summarize_album(tracks)
    assert summary.is_compilation is True


def test_apply_tag_overrides_only_changes_specified_fields(silent_flac: Path) -> None:
    """`retag` must update only the fields you pass; everything else stays."""
    from musickit.metadata import TagOverrides, apply_tag_overrides

    _tag_flac(
        silent_flac,
        {
            "TITLE": "Original Title",
            "ARTIST": "Original Artist",
            "ALBUM": "Original Album",
            "ALBUMARTIST": "Original Album Artist",
            "DATE": "2010",
            "GENRE": "Rock",
        },
    )

    apply_tag_overrides(silent_flac, TagOverrides(album="New Album", year="2024"))

    flac = FLAC(silent_flac)
    assert flac["ALBUM"][0] == "New Album"
    assert flac["DATE"][0] == "2024"
    # Untouched fields preserved.
    assert flac["TITLE"][0] == "Original Title"
    assert flac["ARTIST"][0] == "Original Artist"
    assert flac["ALBUMARTIST"][0] == "Original Album Artist"
    assert flac["GENRE"][0] == "Rock"


def test_apply_tag_overrides_year_normalises_to_4_digits(silent_flac: Path) -> None:
    from musickit.metadata import TagOverrides, apply_tag_overrides

    _tag_flac(silent_flac, {"DATE": "2010"})
    apply_tag_overrides(silent_flac, TagOverrides(year="2024-01-15"))
    flac = FLAC(silent_flac)
    assert flac["DATE"][0] == "2024"


def test_apply_tag_overrides_empty_string_clears_mp4_tag(silent_flac: Path, tmp_path: Path) -> None:
    """`TagOverrides(genre="")` must clear the genre tag on .m4a files.

    The TagOverrides docstring documents empty-string-means-clear, and ID3
    + FLAC honour it. Previously the MP4 path silently no-op'd on empty
    strings via `_set` (which strips and returns early on empty values),
    leaving the old genre in place.
    """
    from musickit.metadata import TagOverrides, apply_tag_overrides

    dst = tmp_path / "track.m4a"
    convert.to_alac(silent_flac, dst)
    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    assert mp4.tags is not None
    mp4.tags["\xa9gen"] = ["Rock"]
    mp4.tags["\xa9nam"] = ["Title"]
    mp4.save()

    apply_tag_overrides(dst, TagOverrides(genre="", title=""))

    mp4 = MP4(dst)
    assert mp4.tags is not None
    assert "\xa9gen" not in mp4.tags
    assert "\xa9nam" not in mp4.tags


def test_round_trip_flac_to_alac_preserves_tags(silent_flac: Path, tmp_path: Path) -> None:
    _tag_flac(
        silent_flac,
        {
            "TITLE": "Track 1",
            "ARTIST": "Artist X",
            "ALBUMARTIST": "Artist X",
            "ALBUM": "Album Y",
            "DATE": "2021",
            "GENRE": "Rock",
            "TRACKNUMBER": "1/10",
            "DISCNUMBER": "1/1",
            "BPM": "120",
            "LABEL": "Label Z",
            "CATALOGNUMBER": "CAT-001",
            "REPLAYGAIN_TRACK_GAIN": "-5.0 dB",
        },
    )

    track = read_source(silent_flac)
    summary = AlbumSummary(
        album="Album Y",
        album_artist="Artist X",
        artist_fallback="Artist X",
        year="2021",
        genre="Rock",
        track_total=10,
        disc_total=1,
        is_compilation=False,
    )

    out = tmp_path / "01 - Track 1.m4a"
    convert.to_alac(silent_flac, out)
    write_mp4_tags(
        out,
        track,
        summary,
        cover_bytes=None,
        cover_mime=None,
        musicbrainz=MusicBrainzIds(album_id="abc-123"),
    )

    mp4 = MP4(out)
    tags = mp4.tags
    assert tags is not None
    assert tags["\xa9nam"][0] == "Track 1"
    assert tags["\xa9ART"][0] == "Artist X"
    assert tags["\xa9alb"][0] == "Album Y"
    assert tags["aART"][0] == "Artist X"
    assert tags["\xa9day"][0] == "2021"
    assert tags["\xa9gen"][0] == "Rock"
    assert tags["trkn"][0] == (1, 10)
    assert tags["disk"][0] == (1, 1)
    assert tags["tmpo"][0] == 120

    assert bytes(tags["----:com.apple.iTunes:LABEL"][0]).decode() == "Label Z"
    assert bytes(tags["----:com.apple.iTunes:CATALOGNUMBER"][0]).decode() == "CAT-001"
    assert bytes(tags["----:com.apple.iTunes:replaygain_track_gain"][0]).decode() == "-5.0 dB"
    assert bytes(tags["----:com.apple.iTunes:MusicBrainz Album Id"][0]).decode() == "abc-123"

    # Confirm the audio is actually ALAC, not lossy.
    assert mp4.info.codec == "alac"
