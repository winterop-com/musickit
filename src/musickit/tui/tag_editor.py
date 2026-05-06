"""In-TUI tag editor — modal screen for editing a track's audio metadata.

Triggered by `e` on a focused tracklist row in `MusickitApp`. Form fields
mirror `TagOverrides`: title, artist, album artist, album, year, genre,
track number / total, disc number / total. Save delegates to
`metadata.apply_tag_overrides`, which writes via mutagen and preserves
fields the user didn't touch.

Subsonic-client mode: refused at the App level — the synthetic
`/subsonic/...` path isn't a real on-disk file we can mutate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

if TYPE_CHECKING:
    from musickit.library import LibraryAlbum, LibraryTrack
    from musickit.metadata import TagOverrides
    from musickit.tui.app import MusickitApp


class TrackTagEditorScreen(ModalScreen[None]):
    """Edit one track's audio tags + persist via `apply_tag_overrides`."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Cancel", show=False),
        Binding("ctrl+s", "save", "Save", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    TrackTagEditorScreen {
        align: center middle;
    }
    TrackTagEditorScreen Vertical#editor {
        width: 70;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    TrackTagEditorScreen #title-bar {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    TrackTagEditorScreen #path-line {
        color: $text-muted;
        margin-bottom: 1;
        text-align: center;
    }
    TrackTagEditorScreen .field-row {
        height: auto;
        margin-bottom: 0;
    }
    TrackTagEditorScreen .field-label {
        width: 16;
        padding: 1 1 0 0;
        color: $text-muted;
    }
    TrackTagEditorScreen Input {
        width: 1fr;
    }
    TrackTagEditorScreen Input.short {
        width: 12;
    }
    TrackTagEditorScreen #status {
        height: 1;
        margin-top: 1;
        text-align: center;
    }
    TrackTagEditorScreen #help {
        margin-top: 1;
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, app_ref: MusickitApp, track: LibraryTrack) -> None:
        super().__init__()
        self._app_ref = app_ref
        self._track = track
        # Capture originals so the save path only writes fields that changed.
        self._orig = {
            "title": track.title or "",
            "artist": track.artist or "",
            "album_artist": track.album_artist or "",
            "album": track.album or "",
            "year": track.year or "",
            "genre": track.genre or "",
            "track_no": str(track.track_no) if track.track_no else "",
            "disc_no": str(track.disc_no) if track.disc_no else "",
        }

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="editor"):
            yield Label("Edit Tags", id="title-bar")
            yield Label(str(self._track.path), id="path-line")
            yield from self._field_row("Title", "title", self._orig["title"])
            yield from self._field_row("Artist", "artist", self._orig["artist"])
            yield from self._field_row("Album Artist", "album_artist", self._orig["album_artist"])
            yield from self._field_row("Album", "album", self._orig["album"])
            yield from self._field_row("Year", "year", self._orig["year"], short=True)
            yield from self._field_row("Genre", "genre", self._orig["genre"])
            yield from self._field_row("Track #", "track_no", self._orig["track_no"], short=True)
            yield from self._field_row("Disc #", "disc_no", self._orig["disc_no"], short=True)
            yield Label("", id="status")
            yield Label("Ctrl+S Save  ·  Esc Cancel", id="help")

    def _field_row(self, label: str, field_id: str, value: str, *, short: bool = False) -> ComposeResult:
        with Horizontal(classes="field-row"):
            yield Label(f"{label}:", classes="field-label")
            yield Input(value=value, id=f"f-{field_id}", classes="short" if short else "")

    def on_mount(self) -> None:  # noqa: D102
        # Land focus on the Title input so the user can start typing right away.
        self.query_one("#f-title", Input).focus()

    def action_save(self) -> None:
        """Validate the form, write tags, patch the in-memory track."""
        from musickit.metadata import apply_tag_overrides

        try:
            overrides = self._build_overrides()
        except _ValidationError as exc:
            self._set_status(f"[red]{exc}[/]")
            return
        if overrides.is_empty():
            self.dismiss(None)
            return
        try:
            apply_tag_overrides(self._track.path, overrides)
        except Exception as exc:  # broad — mutagen + filesystem can throw a lot
            self._set_status(f"[red]save failed: {exc}[/]")
            return
        self._patch_track_in_memory(overrides)
        self._app_ref.notify_track_tags_updated(self._track)
        self.dismiss(None)

    def _build_overrides(self) -> TagOverrides:
        from musickit.metadata import TagOverrides

        kwargs: dict[str, object] = {}
        for field in ("title", "artist", "album_artist", "album", "year", "genre"):
            new_value = self.query_one(f"#f-{field}", Input).value.strip()
            if new_value != self._orig[field]:
                # Empty string clears the tag; non-empty sets it.
                kwargs[field] = new_value
        # Year sanity: 4 digits if non-empty.
        year = kwargs.get("year")
        if isinstance(year, str) and year and not (year.isdigit() and len(year) == 4):
            raise _ValidationError("Year must be 4 digits (e.g. 2007).")
        # Track # / Disc #: integer if non-empty.
        for field in ("track_no", "disc_no"):
            raw = self.query_one(f"#f-{field}", Input).value.strip()
            if raw == self._orig[field]:
                continue
            if not raw:
                # Clearing the number isn't supported in TagOverrides —
                # nothing to do; the existing tag is preserved.
                continue
            if not raw.isdigit() or int(raw) <= 0:
                raise _ValidationError(f"{field.replace('_', ' ').title()} must be a positive integer.")
            kwargs[field] = int(raw)
        return TagOverrides(**kwargs)  # type: ignore[arg-type]

    def _patch_track_in_memory(self, overrides: TagOverrides) -> None:
        """Update the LibraryTrack pydantic model so the UI reflects new tags."""
        if overrides.title is not None:
            self._track.title = overrides.title or None
        if overrides.artist is not None:
            self._track.artist = overrides.artist or None
        if overrides.album_artist is not None:
            self._track.album_artist = overrides.album_artist or None
        if overrides.album is not None:
            self._track.album = overrides.album or None
        if overrides.year is not None:
            self._track.year = overrides.year or None
        if overrides.genre is not None:
            self._track.genre = overrides.genre or None
        if overrides.track_no is not None:
            self._track.track_no = overrides.track_no
        if overrides.disc_no is not None:
            self._track.disc_no = overrides.disc_no

    def _set_status(self, markup: str) -> None:
        self.query_one("#status", Label).update(markup)

    def action_dismiss_screen(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class AlbumTagEditorScreen(ModalScreen[None]):
    """Edit album-wide tags + apply across every track in the album.

    Album-wide fields only: Album, Album Artist, Year, Genre. Per-track
    fields (title, artist, track #) stay per-track and aren't shown here
    — for those use the per-track editor (`e` on a tracklist row).
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Cancel", show=False),
        Binding("ctrl+s", "save", "Save", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    AlbumTagEditorScreen {
        align: center middle;
    }
    AlbumTagEditorScreen Vertical#editor {
        width: 70;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    AlbumTagEditorScreen #title-bar {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    AlbumTagEditorScreen #path-line {
        color: $text-muted;
        margin-bottom: 1;
        text-align: center;
    }
    AlbumTagEditorScreen #scope-line {
        color: $text-muted;
        margin-bottom: 1;
        text-align: center;
    }
    AlbumTagEditorScreen .field-row {
        height: auto;
        margin-bottom: 0;
    }
    AlbumTagEditorScreen .field-label {
        width: 16;
        padding: 1 1 0 0;
        color: $text-muted;
    }
    AlbumTagEditorScreen Input {
        width: 1fr;
    }
    AlbumTagEditorScreen Input.short {
        width: 12;
    }
    AlbumTagEditorScreen #status {
        height: 1;
        margin-top: 1;
        text-align: center;
    }
    AlbumTagEditorScreen #help {
        margin-top: 1;
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, app_ref: MusickitApp, album: LibraryAlbum) -> None:
        super().__init__()
        self._app_ref = app_ref
        self._album = album
        self._orig = {
            "album": album.tag_album or "",
            "album_artist": album.tag_album_artist or "",
            "year": album.tag_year or "",
            "genre": album.tag_genre or "",
        }

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="editor"):
            yield Label("Edit Album Tags", id="title-bar")
            yield Label(str(self._album.path), id="path-line")
            yield Label(f"applies to all {len(self._album.tracks)} track(s)", id="scope-line")
            yield from self._field_row("Album", "album", self._orig["album"])
            yield from self._field_row("Album Artist", "album_artist", self._orig["album_artist"])
            yield from self._field_row("Year", "year", self._orig["year"], short=True)
            yield from self._field_row("Genre", "genre", self._orig["genre"])
            yield Label("", id="status")
            yield Label("Ctrl+S Save  ·  Esc Cancel", id="help")

    def _field_row(self, label: str, field_id: str, value: str, *, short: bool = False) -> ComposeResult:
        with Horizontal(classes="field-row"):
            yield Label(f"{label}:", classes="field-label")
            yield Input(value=value, id=f"f-{field_id}", classes="short" if short else "")

    def on_mount(self) -> None:  # noqa: D102
        self.query_one("#f-album", Input).focus()

    def action_save(self) -> None:
        """Validate, write to every track, patch the in-memory album, rename folder if needed."""
        from musickit.library import RenameError, rename_album_to_match_tags
        from musickit.metadata import apply_tag_overrides

        try:
            overrides = self._build_overrides()
        except _ValidationError as exc:
            self._set_status(f"[red]{exc}[/]")
            return
        if overrides.is_empty():
            self.dismiss(None)
            return
        failures: list[tuple[str, str]] = []
        for track in self._album.tracks:
            try:
                apply_tag_overrides(track.path, overrides)
            except Exception as exc:
                failures.append((track.path.name, str(exc)))
        if failures:
            first_name, first_err = failures[0]
            n = len(failures)
            self._set_status(f"[red]{n} file(s) failed (e.g. {first_name}: {first_err})[/]")
            return
        self._patch_album_in_memory(overrides)

        # Album / album_artist / year changes warrant a folder rename so
        # the on-disk layout matches the tags. Year-only is borderline
        # (the convention is `YYYY - Title`) — included for consistency
        # with the convert pipeline. Genre never affects the path.
        rename_result = None
        triggers = (overrides.album, overrides.album_artist, overrides.year)
        if self._app_ref.library_root is not None and any(t is not None for t in triggers):
            try:
                rename_result = rename_album_to_match_tags(self._album, self._app_ref.library_root)
            except RenameError as exc:
                self._set_status(f"[yellow]tags saved but rename failed: {exc}[/]")
                # Stay in the modal so the user sees the warning; let them
                # press Esc to dismiss when ready.
                return

        self._app_ref.notify_album_tags_updated(self._album, rename_result=rename_result)
        self.dismiss(None)

    def _build_overrides(self) -> TagOverrides:
        from musickit.metadata import TagOverrides

        kwargs: dict[str, object] = {}
        for field in ("album", "album_artist", "year", "genre"):
            new_value = self.query_one(f"#f-{field}", Input).value.strip()
            if new_value != self._orig[field]:
                kwargs[field] = new_value
        year = kwargs.get("year")
        if isinstance(year, str) and year and not (year.isdigit() and len(year) == 4):
            raise _ValidationError("Year must be 4 digits (e.g. 2007).")
        return TagOverrides(**kwargs)  # type: ignore[arg-type]

    def _patch_album_in_memory(self, overrides: TagOverrides) -> None:
        """Update LibraryAlbum + every LibraryTrack so the UI reflects new tags."""
        if overrides.album is not None:
            self._album.tag_album = overrides.album or None
        if overrides.album_artist is not None:
            self._album.tag_album_artist = overrides.album_artist or None
        if overrides.year is not None:
            self._album.tag_year = overrides.year or None
        if overrides.genre is not None:
            self._album.tag_genre = overrides.genre or None
        for track in self._album.tracks:
            if overrides.album is not None:
                track.album = overrides.album or None
            if overrides.album_artist is not None:
                track.album_artist = overrides.album_artist or None
            if overrides.year is not None:
                track.year = overrides.year or None
            if overrides.genre is not None:
                track.genre = overrides.genre or None

    def _set_status(self, markup: str) -> None:
        self.query_one("#status", Label).update(markup)

    def action_dismiss_screen(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class _ValidationError(Exception):
    """Raised by the form's `_build_overrides` to surface form-level errors."""
