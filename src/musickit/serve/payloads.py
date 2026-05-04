"""Subsonic payload builders — album / song / artist dicts shared by endpoints."""

from __future__ import annotations

from typing import Any

from musickit.library.models import LibraryAlbum, LibraryTrack
from musickit.serve.ids import album_id, artist_id, track_id
from musickit.serve.index import IndexCache

_CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".m4b": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
}


def content_type(track: LibraryTrack) -> str:
    """MIME type for a track's container — drives the `Content-Type` of `/stream`."""
    return _CONTENT_TYPES.get(track.path.suffix.lower(), "application/octet-stream")


def suffix(track: LibraryTrack) -> str:
    """File extension without the leading dot, lowercased — Subsonic `suffix` field."""
    return track.path.suffix.lstrip(".").lower()


def track_size_bytes(track: LibraryTrack) -> int:
    """File size in bytes, 0 if the file moved between scans."""
    try:
        return track.path.stat().st_size
    except OSError:
        return 0


def album_payload(album: LibraryAlbum, *, with_songs: bool) -> dict[str, Any]:
    """Subsonic `album` dict — used by getAlbum, getArtist, getAlbumList2, search3."""
    al_id = album_id(album)
    ar_id = artist_id(album.artist_dir)
    duration = int(sum(t.duration_s for t in album.tracks))
    payload: dict[str, Any] = {
        "id": al_id,
        "name": album.tag_album or album.album_dir,
        "artist": album.artist_dir,
        "artistId": ar_id,
        "songCount": album.track_count,
        "duration": duration,
        "coverArt": al_id,
        "created": "1970-01-01T00:00:00.000Z",
    }
    if album.tag_year:
        try:
            payload["year"] = int(album.tag_year)
        except ValueError:
            pass
    if album.tag_genre:
        payload["genre"] = album.tag_genre
    # OpenSubsonic multipleGenres: union across the album's tracks, preserving
    # the order they first appear (Counter would lose order; dict.fromkeys
    # gives us de-dup + insertion-order in one).
    track_genres: dict[str, None] = {}
    for t in album.tracks:
        for g in t.genres:
            if g:
                track_genres[g] = None
    if track_genres:
        payload["genres"] = [{"name": name} for name in track_genres]
    if with_songs:
        payload["song"] = [song_payload(album, t) for t in album.tracks]
    return payload


def song_payload(album: LibraryAlbum, track: LibraryTrack) -> dict[str, Any]:
    """Subsonic `song`/`child` dict — used everywhere a track appears."""
    al_id = album_id(album)
    ar_id = artist_id(album.artist_dir)
    payload: dict[str, Any] = {
        "id": track_id(track),
        "parent": al_id,
        "isDir": False,
        "title": track.title or track.path.stem,
        "album": album.tag_album or album.album_dir,
        "artist": track.artist or album.artist_dir,
        "isVideo": False,
        "type": "music",
        "albumId": al_id,
        "artistId": ar_id,
        "coverArt": al_id,
        "duration": int(track.duration_s) if track.duration_s else 0,
        "size": track_size_bytes(track),
        "suffix": suffix(track),
        "contentType": content_type(track),
        "path": (
            str(track.path.relative_to(album.path.parent.parent))
            if track.path.is_relative_to(album.path.parent.parent)
            else track.path.name
        ),
    }
    if track.track_no is not None:
        payload["track"] = track.track_no
    if track.disc_no is not None:
        payload["discNumber"] = track.disc_no
    if track.year:
        try:
            payload["year"] = int(track.year)
        except ValueError:
            pass
    track_genre = track.genre or album.tag_genre
    if track_genre:
        payload["genre"] = track_genre
    # OpenSubsonic multipleGenres: per-track list. Falls back to the
    # album-level genre when the track itself only has the legacy single.
    if track.genres:
        payload["genres"] = [{"name": g} for g in track.genres]
    elif track_genre:
        payload["genres"] = [{"name": track_genre}]
    return payload


def artist_summary(cache: IndexCache, ar_id: str) -> dict[str, Any]:
    """Subsonic `artist` dict (no album list) used by getArtists / getIndexes / search3."""
    return {
        "id": ar_id,
        "name": cache.artist_name_by_id[ar_id],
        "albumCount": len(cache.artists_by_id[ar_id]),
        "coverArt": ar_id,
    }
