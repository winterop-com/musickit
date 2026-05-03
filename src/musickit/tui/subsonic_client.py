"""Tiny Subsonic API client — read-only browsing + stream URLs for the TUI.

Maps Subsonic responses into the same `LibraryIndex` / `LibraryAlbum` /
`LibraryTrack` shapes the existing widgets/formatters consume, so the
TUI doesn't need a parallel "remote track" type. The only difference
is `LibraryTrack.stream_url` — set here, read by `MusickitApp._play_current`
when present.
"""

from __future__ import annotations

import logging
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
    """Read-only Subsonic API client. Used by `musickit tui --server`."""

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        http: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.http = http or httpx.Client(timeout=timeout)

    def _auth_params(self) -> dict[str, str]:
        return {
            "u": self.user,
            "p": self.password,
            "v": "1.16.1",
            "c": "musickit-tui",
            "f": "json",
        }

    def _get(self, endpoint: str, **extra: str | int) -> dict[str, Any]:
        params: Mapping[str, str | int] = {**self._auth_params(), **extra}
        try:
            resp = self.http.get(f"{self.base_url}/rest/{endpoint}", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SubsonicError(f"HTTP error: {exc}") from exc
        try:
            envelope = resp.json()["subsonic-response"]
        except (KeyError, ValueError) as exc:
            raise SubsonicError(f"malformed response: {resp.text[:120]}") from exc
        if envelope.get("status") != "ok":
            err = envelope.get("error", {})
            raise SubsonicError(f"code {err.get('code')}: {err.get('message', 'unknown error')}")
        return envelope  # type: ignore[no-any-return]

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

    def cover_url(self, item_id: str, *, size: int | None = None) -> str:
        """Build the auth-loaded `/rest/getCoverArt` URL."""
        params: dict[str, str | int] = {**self._auth_params(), "id": item_id}
        if size is not None:
            params["size"] = size
        return f"{self.base_url}/rest/getCoverArt?{urlencode(params)}"


def build_index(
    client: SubsonicClient,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> LibraryIndex:
    """Walk the Subsonic API to build a `LibraryIndex`.

    Round-trip cost: 1 (getArtists) + N_artists (getArtist) + N_albums
    (getAlbum). For a typical library that's 100s of requests — slow over
    Tailscale but tolerable for v1. Lazy per-album loading is a follow-up.

    `on_progress(album_label, idx, total)` mirrors the local-scan callback
    so the existing TUI overlay drives both paths unchanged.
    """
    artists = client.get_artists()

    # Pre-walk to count albums so progress shows the real total.
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
        try:
            full = client.get_album(album_meta["id"])
        except SubsonicError as exc:  # pragma: no cover — skip the album, don't crash
            log.warning("getAlbum(%s) failed: %s", album_meta.get("id"), exc)
            continue
        library_albums.append(_album_from_subsonic(client, artist, full))

    library_albums.sort(key=lambda a: (a.artist_dir.lower(), a.album_dir.lower()))
    # `LibraryIndex.root` is a Path, but in client mode there's no real
    # filesystem root — store the server URL as a Path to keep typing happy.
    return LibraryIndex(root=Path(client.base_url), albums=library_albums)


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
    )
