"""Tiny Subsonic API client — read-only browsing + stream URLs for the TUI.

Maps Subsonic responses into the same `LibraryIndex` / `LibraryAlbum` /
`LibraryTrack` shapes the existing widgets/formatters consume, so the
TUI doesn't need a parallel "remote track" type. The only difference
is `LibraryTrack.stream_url` — set here, read by `MusickitApp._play_current`
when present.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack

if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)


class SubsonicError(Exception):
    """Raised when a Subsonic call returns status='failed' or HTTP error."""


class SubsonicClient:
    """Read-only Subsonic API client. Used by `musickit tui --subsonic`.

    Supports two auth shapes per the Subsonic spec:
      - Password (`p=...`) — plaintext.
      - Token (`t=md5(password+salt), s=salt`) — what `state.toml`-saved
        sessions reuse. Avoids storing the raw password on disk; the
        saved (salt, token) pair authenticates the same account but
        can't be replayed against other services if leaked.

    Construct with `password=...` or `token=..., salt=...` — never both.
    """

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str | None = None,
        *,
        token: str | None = None,
        salt: str | None = None,
        http: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        if password is None and (token is None or salt is None):
            msg = "SubsonicClient requires either `password` or both `token` and `salt`"
            raise ValueError(msg)
        if password is not None and (token is not None or salt is not None):
            msg = "Pass either `password` OR (`token`, `salt`), not both"
            raise ValueError(msg)
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.token = token
        self.salt = salt
        self.http = http or httpx.Client(timeout=timeout)

    @classmethod
    def derive_token(cls, password: str) -> tuple[str, str]:
        """Return `(token, salt)` derived from `password` for token-auth saves.

        `salt` is 16 random hex chars; `token` is `md5(password + salt)`.
        Used by the CLI's `--save` path to convert a one-time password
        into a persistable token + salt pair.
        """
        salt = secrets.token_hex(8)
        token = hashlib.md5((password + salt).encode("utf-8"), usedforsecurity=False).hexdigest()
        return token, salt

    def auth_for_state(self) -> dict[str, str] | None:
        """Return `(host, user, token, salt)` suitable for state.toml.

        Returns None when the client was constructed with a plaintext
        password (CLI must call `derive_token()` to produce a saveable
        pair). Token-mode clients return their existing values directly.
        """
        if self.token is None or self.salt is None:
            return None
        return {
            "host": self.base_url,
            "user": self.user,
            "token": self.token,
            "salt": self.salt,
        }

    def _auth_params(self) -> dict[str, str]:
        base = {
            "u": self.user,
            "v": "1.16.1",
            "c": "musickit-tui",
            "f": "json",
        }
        if self.token is not None and self.salt is not None:
            base["t"] = self.token
            base["s"] = self.salt
        else:
            assert self.password is not None  # __init__ enforced one of the two
            base["p"] = self.password
        return base

    def _get(self, endpoint: str, **extra: str | int) -> dict[str, Any]:
        params: Mapping[str, str | int] = {**self._auth_params(), **extra}
        try:
            resp = self.http.get(f"{self.base_url}/rest/{endpoint}", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SubsonicError(f"HTTP error: {exc}") from exc
        # Defensive parse — beyond the obvious JSON-decode error, a malformed
        # server might return `[]` (TypeError on dict-indexing), or
        # `{"subsonic-response": []}` (AttributeError on `.get`), or a string
        # status that doesn't compare normally. Funnel everything into
        # SubsonicError so callers don't have to handle bare exceptions.
        try:
            parsed = resp.json()
            if not isinstance(parsed, dict):
                raise TypeError("top-level response is not a JSON object")
            envelope = parsed["subsonic-response"]
            if not isinstance(envelope, dict):
                raise TypeError("subsonic-response is not a JSON object")
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise SubsonicError(f"malformed response: {resp.text[:120]}") from exc
        if envelope.get("status") != "ok":
            err = envelope.get("error") or {}
            if not isinstance(err, dict):
                err = {}
            raise SubsonicError(f"code {err.get('code')}: {err.get('message', 'unknown error')}")
        return envelope

    def ping(self) -> None:
        """Auth check + connectivity. Raises SubsonicError on any failure."""
        self._get("ping")

    def get_artists(self) -> list[dict[str, Any]]:
        """Flat artist list — flattens the alphabetical buckets."""
        body = self._get("getArtists")
        artists: list[dict[str, Any]] = []
        for bucket in body.get("artists", {}).get("index", []):
            artists.extend(bucket.get("artist", []))
        return artists

    def get_artist(self, artist_id: str) -> dict[str, Any]:
        """One artist with its album list (no tracks yet)."""
        body = self._get("getArtist", id=artist_id)
        return body.get("artist", {})  # type: ignore[no-any-return]

    def get_album(self, album_id: str) -> dict[str, Any]:
        """One album with its tracks."""
        body = self._get("getAlbum", id=album_id)
        return body.get("album", {})  # type: ignore[no-any-return]

    def stream_url(self, song_id: str) -> str:
        """Build the auth-loaded `/rest/stream` URL for `AudioPlayer.play()`."""
        params = {**self._auth_params(), "id": song_id}
        return f"{self.base_url}/rest/stream?{urlencode(params)}"

    def get_lyrics(self, song_id: str) -> tuple[list[dict[str, Any]], bool]:
        """Fetch structured lyrics via `getLyricsBySongId`.

        Returns `(lines, synced)` where `lines` is the raw OpenSubsonic
        `line[]` array (each item `{start?: int, value: str}`) and
        `synced` is True when the server promoted the body. Empty list +
        False when no lyrics are available; never raises for "no match".
        """
        try:
            body = self._get("getLyricsBySongId", id=song_id)
        except SubsonicError:
            return [], False
        lyrics_list = body.get("lyricsList", {})
        structured = lyrics_list.get("structuredLyrics") or []
        if not structured:
            return [], False
        first = structured[0]
        lines_raw = first.get("line", [])
        lines = [line for line in lines_raw if isinstance(line, dict)]
        synced = bool(first.get("synced"))
        return lines, synced

    def cover_url(self, item_id: str, *, size: int | None = None) -> str:
        """Build the auth-loaded `/rest/getCoverArt` URL."""
        params: dict[str, str | int] = {**self._auth_params(), "id": item_id}
        if size is not None:
            params["size"] = size
        return f"{self.base_url}/rest/getCoverArt?{urlencode(params)}"

    def close(self) -> None:
        """Close the underlying httpx connection pool. Idempotent."""
        try:
            self.http.close()
        except Exception:  # pragma: no cover — best effort on shutdown
            pass


def build_index(
    client: SubsonicClient,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
    eager: bool = False,
) -> LibraryIndex:
    """Walk the Subsonic API to build a `LibraryIndex`.

    Default (`eager=False`): fetch only artists + their album metadata.
    Tracks are populated lazily by `hydrate_album_tracks` when the user
    opens an album. Round-trip cost: 1 + N_artists. For an 800-album
    library that's ~80 calls instead of ~900.

    `eager=True`: also fetch every album's tracks at launch time.
    Slower (1 + N_artists + N_albums calls) but every album is ready
    to play with no further network IO. Useful when the client wants
    to support full-library shuffle without waiting on hydration.

    `on_progress(album_label, idx, total)` mirrors the local-scan callback
    so the existing TUI overlay drives both paths unchanged.
    """
    artists = client.get_artists()

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for artist in artists:
        try:
            artist_data = client.get_artist(artist["id"])
        except SubsonicError as exc:  # pragma: no cover — single artist failure is non-fatal
            log.warning("getArtist(%s) failed: %s", artist.get("id"), exc)
            continue
        for album_meta in artist_data.get("album", []):
            pairs.append((artist, album_meta))

    total = len(pairs)
    library_albums: list[LibraryAlbum] = []
    for idx, (artist, album_meta) in enumerate(pairs, start=1):
        if on_progress is not None:
            on_progress(album_meta.get("name", "?"), idx, total)
        if eager:
            try:
                full = client.get_album(album_meta["id"])
            except SubsonicError as exc:  # pragma: no cover — skip the album, don't crash
                log.warning("getAlbum(%s) failed: %s", album_meta.get("id"), exc)
                continue
            library_albums.append(_album_from_subsonic(client, artist, full))
        else:
            library_albums.append(_shell_album_from_subsonic(artist, album_meta))

    library_albums.sort(key=lambda a: (a.artist_dir.lower(), a.album_dir.lower()))
    # `LibraryIndex.root` is a Path, but in client mode there's no real
    # filesystem root — store the server URL as a Path to keep typing happy.
    return LibraryIndex(root=Path(client.base_url), albums=library_albums)


def hydrate_album_tracks(client: SubsonicClient, album: LibraryAlbum) -> None:
    """Populate `album.tracks` in place from `getAlbum?id=...`. No-op if already loaded."""
    if not album.subsonic_id:
        return
    if album.tracks:
        return
    full = client.get_album(album.subsonic_id)
    artist = {"id": "", "name": album.artist_dir}
    populated = _album_from_subsonic(client, artist, full)
    album.tracks = populated.tracks
    album.track_count = len(populated.tracks)
    if not album.tag_year and populated.tag_year:
        album.tag_year = populated.tag_year


def _shell_album_from_subsonic(
    artist: dict[str, Any],
    album_meta: dict[str, Any],
) -> LibraryAlbum:
    """Build a track-less `LibraryAlbum` from `getArtist`'s embedded album metadata."""
    artist_name = artist.get("name", "?")
    album_name = album_meta.get("name", "?")
    year_raw = album_meta.get("year")
    year = str(year_raw) if year_raw else None
    return LibraryAlbum(
        path=Path("/subsonic") / artist_name / album_name,
        artist_dir=artist_name,
        album_dir=album_name,
        tag_album=album_name,
        tag_year=year,
        tag_album_artist=artist_name,
        track_count=int(album_meta.get("songCount", 0) or 0),
        is_compilation=False,
        has_cover=bool(album_meta.get("coverArt")),
        tracks=[],
        warnings=[],
        subsonic_id=str(album_meta["id"]) if album_meta.get("id") else None,
    )


def _album_from_subsonic(
    client: SubsonicClient,
    artist: dict[str, Any],
    album_data: dict[str, Any],
) -> LibraryAlbum:
    """Convert a `getAlbum` response into a `LibraryAlbum` + `LibraryTracks`."""
    artist_name = artist.get("name", "?")
    album_name = album_data.get("name", "?")
    year_raw = album_data.get("year")
    year = str(year_raw) if year_raw else None

    tracks: list[LibraryTrack] = []
    for song in album_data.get("song", []):
        title = song.get("title", song["id"])
        suffix = song.get("suffix") or "m4a"
        # Synthetic path — never read from disk in client mode, but
        # `LibraryTrack.path` is required and a few UI fallbacks read
        # `.stem` / `.suffix` when title/extension are missing.
        synthetic_path = Path("/subsonic") / artist_name / album_name / f"{title}.{suffix}"
        tracks.append(
            LibraryTrack(
                path=synthetic_path,
                title=song.get("title"),
                artist=song.get("artist", artist_name),
                album=album_name,
                year=year,
                track_no=song.get("track"),
                disc_no=song.get("discNumber"),
                duration_s=float(song.get("duration", 0)),
                has_cover=bool(song.get("coverArt")),
                cover_pixels=0,
                stream_url=client.stream_url(song["id"]),
            )
        )

    fake_root = Path("/subsonic")
    return LibraryAlbum(
        path=fake_root / artist_name / album_name,
        artist_dir=artist_name,
        album_dir=album_name,
        tag_album=album_name,
        tag_year=year,
        tag_album_artist=artist_name,
        track_count=len(tracks),
        is_compilation=False,
        has_cover=bool(album_data.get("coverArt")),
        tracks=tracks,
        warnings=[],
        subsonic_id=str(album_data["id"]) if album_data.get("id") else None,
    )
