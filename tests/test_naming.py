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


def test_va_aliases_include_localised_forms():
    """Real rips ship with the localised form of 'Various Artists'."""
    for alias in [
        "Blandade Artister",  # Swedish
        "blandade artister",
        "Verschiedene Interpreten",  # German
        "Varios Artistas",  # Spanish
        "Vari Artisti",  # Italian
        "Artistes Divers",  # French
    ]:
        assert is_various_artists(alias) is True, alias


def test_folder_name_implies_va_for_scene_naming():
    """Scene-rip dir names like `VA-Absolute_Music_60` should signal compilation."""
    from musickit.naming import folder_name_implies_va

    assert folder_name_implies_va("VA-Absolute_Music_60") is True
    assert folder_name_implies_va("VA_Absolute_Music_47") is True
    assert folder_name_implies_va("VA - Greatest Hits 2024") is True
    assert folder_name_implies_va("V.A. - Best Of") is True
    assert folder_name_implies_va("Various - Top 40") is True
    assert folder_name_implies_va("Various Artists - Hits") is True
    # Real artists named starting with VA-something shouldn't be misclassified.
    assert folder_name_implies_va("Vampire Weekend - Modern Vampires") is False
    assert folder_name_implies_va("Vance Joy") is False
    assert folder_name_implies_va("Variety Pack") is False
    assert folder_name_implies_va("") is False


def test_smart_title_case_only_acts_on_all_lowercase():
    """Title-case fires only when source has zero uppercase — protects real casing."""
    from musickit.naming import smart_title_case

    # All-lowercase → titled.
    assert smart_title_case("hang with me") == "Hang With Me"
    assert smart_title_case("robyn") == "Robyn"
    assert smart_title_case("håkan hellström") == "Håkan Hellström"
    # Apostrophe contractions stay correct (not `Don'T`).
    assert smart_title_case("don't stop me now") == "Don't Stop Me Now"
    assert smart_title_case("rock'n'roll") == "Rock'n'Roll"
    # Real casing → preserved.
    assert smart_title_case("AC/DC") == "AC/DC"
    assert smart_title_case("ABBA") == "ABBA"
    assert smart_title_case("iPhone") == "iPhone"
    assert smart_title_case("R.E.M.") == "R.E.M."
    assert smart_title_case("Imagine Dragons") == "Imagine Dragons"
    # Edges.
    assert smart_title_case(None) is None
    assert smart_title_case("") == ""


def test_scene_domain_detection_handles_multilabel_hosts():
    """`www.0dayvinyls.org` survived the single-dot regex; multi-dot now caught."""
    from musickit.naming import is_scene_domain_artist

    assert is_scene_domain_artist("www.0dayvinyls.org") is True
    assert is_scene_domain_artist("releases.scene.cc") is True
    # `R.E.M.` would still be safe — last segment is 1-letter, not a TLD.
    assert is_scene_domain_artist("R.E.M.") is False


def test_scene_domain_artist_detection():
    """Domain-shaped 'artists' (vandalism by rip groups) detected as fake."""
    from musickit.naming import is_scene_domain_artist

    assert is_scene_domain_artist("LanzamientosMp3.es") is True
    assert is_scene_domain_artist("boxset.me") is True
    assert is_scene_domain_artist("mp3hosting.cc") is True
    assert is_scene_domain_artist("rutracker.org") is True
    # Not a domain — real artist names with periods stay intact.
    assert is_scene_domain_artist("R.E.M.") is False
    assert is_scene_domain_artist("St. Vincent") is False
    assert is_scene_domain_artist("Mr. Big") is False
    assert is_scene_domain_artist("Imagine Dragons") is False
    assert is_scene_domain_artist(None) is False
    assert is_scene_domain_artist("") is False


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


def test_track_filename_widens_padding_for_albums_with_100_plus_tracks():
    """100-track albums need 3-digit padding so alphabetical sort matches track order."""
    # Single-digit and 2-digit albums keep the original 2-wide format.
    assert track_filename(1, "Track", track_total=12) == "01 - Track.m4a"
    assert track_filename(99, "Track", track_total=99) == "99 - Track.m4a"
    # 100+: width grows to 3, so 1 → 001 ... 99 → 099 ... 100 → 100, sorts correctly.
    assert track_filename(1, "Track", track_total=100) == "001 - Track.m4a"
    assert track_filename(99, "Track", track_total=100) == "099 - Track.m4a"
    assert track_filename(100, "Track", track_total=100) == "100 - Track.m4a"
    # Multi-disc + 100-track album: disc stays 2-wide, track widens to 3.
    assert track_filename(50, "Track", disc_no=2, disc_total=2, track_total=100) == "02-050 - Track.m4a"


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
