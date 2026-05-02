"""Filesystem-safe name building."""

from musickit.naming import (
    VARIOUS_ARTISTS,
    album_folder,
    artist_folder,
    clean_folder_album_name,
    is_various_artists,
    sanitize_component,
    track_filename,
)


def test_clean_folder_album_name_strips_codec_and_year():
    assert clean_folder_album_name("VA - Best of Dance Hits of the 90-98's (1998)[FLAC]") == (
        "Best of Dance Hits of the 90-98's",
        "1998",
    )
    assert clean_folder_album_name("Album Name (2012) [FLAC]") == ("Album Name", "2012")
    assert clean_folder_album_name("(1998) Artist - Album [16Bit-44.1kHz]") == ("Artist - Album", "1998")
    assert clean_folder_album_name("Plain Folder") == ("Plain Folder", None)
    # Scene-site tags get stripped.
    assert clean_folder_album_name("[nextorrent.com] 7Os8Os9Os") == ("7Os8Os9Os", None)
    assert clean_folder_album_name("[example.org] Some Album (2020)") == ("Some Album", "2020")
    # Non-domain bracketed annotations are preserved (might be meaningful).
    assert clean_folder_album_name("Album [Live]") == ("Album [Live]", None)
    assert clean_folder_album_name("Album [PMEDIA]") == ("Album [PMEDIA]", None)


def test_va_aliases_collapse_to_various_artists():
    for alias in ["VA", "v.a.", "V/A", "Various", "various artists", "VARIOUS ARTIST"]:
        assert is_various_artists(alias) is True


def test_non_va_artists_pass_through():
    assert is_various_artists("Imagine Dragons") is False
    assert is_various_artists(None) is False
    assert is_various_artists("") is False


def test_artist_folder_routes_va_to_canonical_name():
    assert artist_folder("VA", None) == VARIOUS_ARTISTS
    assert artist_folder("Imagine Dragons", None) == "Imagine Dragons"
    assert artist_folder(None, "Imagine Dragons") == "Imagine Dragons"
    assert artist_folder(None, None) == "Unknown Artist"
    # Some rips leave album_artist blank and stamp every track's artist as VA —
    # fallback path should still resolve to the canonical Various Artists folder.
    assert artist_folder(None, "VA") == VARIOUS_ARTISTS
    assert artist_folder(None, "Various") == VARIOUS_ARTISTS
    # Compilation flag wins regardless of fallback: a tagless MP3 mix where
    # every track is by a different real artist still belongs in `Various Artists`.
    assert artist_folder(None, "Blockbuster", is_compilation=True) == VARIOUS_ARTISTS


def test_album_folder_prefixes_year_for_chronological_sort():
    assert album_folder("Night Visions", "2012") == "2012 - Night Visions"
    assert album_folder("Night Visions", 2012) == "2012 - Night Visions"
    assert album_folder("Night Visions", "2012-09-04") == "2012 - Night Visions"


def test_album_folder_omits_year_when_unknown():
    assert album_folder("Night Visions", None) == "Night Visions"
    assert album_folder("Night Visions", "") == "Night Visions"
    assert album_folder("Night Visions", "not-a-year") == "Night Visions"


def test_sanitize_replaces_forbidden_characters():
    assert sanitize_component("Smoke / Mirrors") == "Smoke - Mirrors"
    assert sanitize_component("a:b") == "a -b"
    assert sanitize_component("foo*?<>|") == "foo-"
    # Double-space artifact (where the ripper turned `/` into `  `) collapses cleanly.
    assert sanitize_component("Nothing Left To Say  Rocks") == "Nothing Left To Say Rocks"


def test_track_filename_basic_format():
    assert track_filename(1, "Radioactive") == "01 - Radioactive.m4a"
    assert track_filename(11, "Nothing Left To Say  Rocks") == "11 - Nothing Left To Say Rocks.m4a"


def test_track_filename_disc_prefix_only_when_multi_disc():
    assert track_filename(1, "Track", disc_no=1, disc_total=1) == "01 - Track.m4a"
    assert track_filename(1, "Track", disc_no=2, disc_total=2) == "02-01 - Track.m4a"
    assert track_filename(1, "Track", disc_no=None, disc_total=2) == "01 - Track.m4a"
    # Two-digit padded across both disc and track for consistency.
    assert track_filename(11, "Track", disc_no=1, disc_total=2) == "01-11 - Track.m4a"


def test_track_filename_handles_missing_inputs():
    assert track_filename(None, None) == "00 - Untitled.m4a"
    assert track_filename(0, "") == "00 - Untitled.m4a"


def test_track_filename_honours_extension():
    assert track_filename(1, "Hi", extension=".mp3") == "01 - Hi.mp3"
    assert track_filename(1, "Hi", extension="mp3") == "01 - Hi.mp3"


def test_track_filename_includes_artist_for_compilations():
    # Single-artist album: artist not passed → traditional `NN - Title` form.
    assert track_filename(5, "Guilty") == "05 - Guilty.m4a"
    # VA / compilation: artist sandwiched between track and title.
    assert track_filename(5, "Guilty", artist="Teddy Swims") == "05 - Teddy Swims - Guilty.m4a"
    # Multi-disc compilation.
    assert (
        track_filename(5, "Guilty", artist="Teddy Swims", disc_no=2, disc_total=2) == "02-05 - Teddy Swims - Guilty.m4a"
    )
    # Artist gets the same sanitization as title (slashes → dashes).
    assert track_filename(1, "Title", artist="A/C") == "01 - A-C - Title.m4a"
