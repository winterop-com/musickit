"""Auto-fixes for the deterministic warnings — MB year + tag/path rename."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from musickit import naming
from musickit.library.models import LibraryAlbum, LibraryIndex
from musickit.library.scan import _split_dir_year
from musickit.metadata import TagOverrides, apply_tag_overrides

if TYPE_CHECKING:
    from rich.console import Console

FixProgressCallback = Callable[[LibraryAlbum, int, int], None]
"""`(album, idx, total)` — called once per flagged album, idx 1-indexed."""


def fix_index(
    index: LibraryIndex,
    *,
    dry_run: bool = False,
    console: Console | None = None,
    year_lookup: object | None = None,
    prefer_dirname: bool = False,
    on_album: FixProgressCallback | None = None,
) -> list[str]:
    """Apply deterministic fixes to every flagged album in `index`.

    Returns a list of human-readable action lines. `year_lookup` is the
    MusicBrainz year-lookup callable (defaults to
    `enrich.musicbrainz.lookup_release_year` — injectable for tests).

    `prefer_dirname=True` inverts the tag/path-mismatch resolution: tags
    get rewritten from the dir name instead of the dir being renamed from
    the tag. Use this when you've hand-curated the directory layout and
    want the tags to follow.

    `on_album(album, idx, total)` fires once per FLAGGED album right
    before its fixes run; clean albums (no warnings) are skipped silently
    and don't count against the total. Used by the CLI to drive a
    progress bar through the slow MB lookups.
    """
    if year_lookup is None:
        from musickit.enrich.musicbrainz import lookup_release_year

        year_lookup = lookup_release_year

    flagged = [a for a in index.albums if a.warnings]
    total = len(flagged)
    actions: list[str] = []
    for idx, album in enumerate(flagged, start=1):
        if on_album is not None:
            on_album(album, idx, total)
        actions.extend(
            fix_album(
                album,
                dry_run=dry_run,
                console=console,
                year_lookup=year_lookup,
                prefer_dirname=prefer_dirname,
            )
        )
    return actions


def fix_album(
    album: LibraryAlbum,
    *,
    dry_run: bool = False,
    console: Console | None = None,
    year_lookup: object,
    prefer_dirname: bool = False,
) -> list[str]:
    """Apply fixes to one album. Returns the action lines performed (or planned)."""
    actions: list[str] = []
    label = f"{album.artist_dir} / {album.album_dir}"

    # Missing-year fixes go first so the rename below sees the new year.
    if any("missing year" in w for w in album.warnings):
        new_year = _fix_missing_year(album, dry_run=dry_run, year_lookup=year_lookup)
        if new_year:
            actions.append(f"{label}: year ← {new_year} (musicbrainz)")
            if console is not None:
                console.print(f"[green]✓[/green] {label}: year ← {new_year} (musicbrainz)")

    has_mismatch = any(w.startswith("tag/path mismatch") for w in album.warnings)
    if prefer_dirname:
        # Push dir-name → tags. Year set by MB above (if any) is preserved
        # only if the dir has no leading year prefix.
        if has_mismatch:
            updated = _fix_retag_to_match_dir(album, dry_run=dry_run)
            if updated:
                tag_album, tag_year = updated
                msg = f"{label}: tag ← album={tag_album!r}"
                if tag_year:
                    msg += f", year={tag_year}"
                actions.append(msg)
                if console is not None:
                    console.print(f"[green]✓[/green] {msg}")
    else:
        # Default: tag wins, rename the dir to match.
        if has_mismatch or actions:
            renamed = _fix_rename_to_match_tag(album, dry_run=dry_run)
            if renamed:
                actions.append(f"{label}: renamed dir → {renamed}")
                if console is not None:
                    console.print(f"[green]✓[/green] {label}: renamed dir → {renamed}")

    return actions


def _fix_missing_year(
    album: LibraryAlbum,
    *,
    dry_run: bool,
    year_lookup: object,
) -> str | None:
    if not album.tag_album:
        return None
    artist_query = album.tag_album_artist or album.artist_dir
    if naming.is_various_artists(artist_query):
        artist_query = "Various Artists"
    year = year_lookup(album.tag_album, artist_query)  # type: ignore[operator]
    if not isinstance(year, str) or not year:
        return None
    if dry_run:
        return year
    overrides = TagOverrides(year=year)
    for track in album.tracks:
        try:
            apply_tag_overrides(track.path, overrides)
        except Exception:  # pragma: no cover — surface elsewhere
            return None
    # Reflect the change in the in-memory model so downstream rename uses it.
    for track in album.tracks:
        track.year = year
    album.tag_year = year
    return year


def _fix_retag_to_match_dir(album: LibraryAlbum, *, dry_run: bool) -> tuple[str, str | None] | None:
    """Write the dir-name's album+year into every track's tags.

    Returns `(album_str, year_str_or_None)` if anything was (or would be)
    written, else None. Used by `library --fix --prefer-dirname` when the
    user has hand-curated the directory layout and wants tags to follow.
    """
    year_from_dir, album_from_dir = _split_dir_year(album.album_dir)
    if not album_from_dir:
        return None
    if album.tag_album == album_from_dir and (year_from_dir is None or album.tag_year == year_from_dir):
        return None
    if dry_run:
        return (album_from_dir, year_from_dir)
    overrides = TagOverrides(album=album_from_dir, year=year_from_dir)
    for track in album.tracks:
        try:
            apply_tag_overrides(track.path, overrides)
        except Exception:  # pragma: no cover — surface elsewhere
            return None
    album.tag_album = album_from_dir
    if year_from_dir is not None:
        album.tag_year = year_from_dir
    for track in album.tracks:
        track.album = album_from_dir
        if year_from_dir is not None:
            track.year = year_from_dir
    return (album_from_dir, year_from_dir)


def _fix_rename_to_match_tag(album: LibraryAlbum, *, dry_run: bool) -> str | None:
    """Rename `album.path` to `YYYY - Album` based on current tag values."""
    if not album.tag_album:
        return None
    new_name = naming.album_folder(album.tag_album, album.tag_year)
    if new_name == album.album_dir:
        return None
    new_path = album.path.parent / new_name
    if new_path.exists() and new_path.resolve() != album.path.resolve():
        return None  # don't clobber an existing dir
    if dry_run:
        return new_name
    album.path.rename(new_path)
    album.path = new_path
    album.album_dir = new_name
    return new_name
