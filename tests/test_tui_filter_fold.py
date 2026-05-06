"""TUI filter helper — diacritic folding + casefold for `/`-bar matching."""

from __future__ import annotations

from musickit.tui.filter import fold, matches


def test_fold_strips_diacritics() -> None:
    """Composed accented chars decompose; combining marks are dropped."""
    assert fold("Beyoncé") == "beyonce"
    assert fold("Sigur Rós") == "sigur ros"
    assert fold("José González") == "jose gonzalez"


def test_fold_handles_already_ascii() -> None:
    """No diacritics to strip -> just casefold."""
    assert fold("ABBA") == "abba"
    assert fold("The Beatles") == "the beatles"


def test_fold_lowercases_with_unicode_aware_casefold() -> None:
    """`casefold` (not `lower`) handles tricky cases like German `ß` -> `ss`."""
    assert fold("Straße") == "strasse"


def test_fold_empty_string() -> None:
    assert fold("") == ""


def test_matches_substring_after_folding() -> None:
    """Real-world use case: `beyonce` finds `Beyoncé`."""
    assert matches("beyonce", "Beyoncé")
    assert matches("rOs", "Sigur Rós")
    assert matches("gonza", "González, José")


def test_matches_empty_needle_is_truthy_passthrough() -> None:
    """An empty filter expression matches everything (no-op sentinel)."""
    assert matches("", "anything")
    assert matches("", "")


def test_matches_no_match_when_substring_missing() -> None:
    assert not matches("xxx", "Beyoncé")
    assert not matches("metallica", "Sigur Rós")


def test_matches_accented_needle_matches_stripped_haystack() -> None:
    """A user typing the accented form still finds the unaccented tag."""
    assert matches("Rós", "Sigur Ros")
    assert matches("González", "Jose Gonzalez")


def test_matches_multi_token_and_finds_non_adjacent_words() -> None:
    """Each whitespace-split token is its own substring; word order + adjacency don't matter."""
    assert matches("daft homework", "Daft Punk - Homework")
    assert matches("homework daft", "Daft Punk - Homework")
    assert matches("punk home", "Daft Punk - Homework")


def test_matches_multi_token_requires_every_token() -> None:
    """If any token is missing, the whole match fails."""
    assert not matches("daft music", "Daft Punk - Homework")
    assert not matches("homework metallica", "Daft Punk - Homework")


def test_matches_multi_token_works_with_diacritics() -> None:
    """Multi-token AND composes with diacritic folding."""
    assert matches("sigur takk", "Sigur Rós - Takk...")
    assert matches("beyonce halo", "Beyoncé - Halo")


def test_matches_whitespace_only_needle_passes_through() -> None:
    """`"   "` is treated as no-filter — same as empty string."""
    assert matches("   ", "anything")
