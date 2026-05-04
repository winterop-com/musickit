r"""Lyrics endpoints — `getLyrics` (legacy) + `getLyricsBySongId` (OpenSubsonic).

We read embedded lyrics from `\xa9lyr` (MP4) / `USLT` (ID3) / `LYRICS`
(FLAC Vorbis comment) during the library scan and stash them on
`LibraryTrack.lyrics`. Both endpoints serve from that cache — no
external lookups in v1 (LRCLIB integration is on the roadmap).

`getLyrics?artist=&title=` is the original Subsonic shape — fuzzy
artist+title match. `getLyricsBySongId?id=` is the OpenSubsonic
extension that uses our stable track ID directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from musickit.library.models import LibraryAlbum, LibraryTrack
from musickit.serve.app import envelope, error_envelope
from musickit.serve.index import IndexCache

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _find_by_artist_title(cache: IndexCache, artist: str, title: str) -> tuple[LibraryAlbum, LibraryTrack] | None:
    """Case-insensitive lookup by artist + title across the cache."""
    artist_l = artist.casefold()
    title_l = title.casefold()
    for album, track in cache.tracks_by_id.values():
        if (track.title or "").casefold() != title_l:
            continue
        track_artist = (track.artist or album.artist_dir).casefold()
        if track_artist != artist_l and album.artist_dir.casefold() != artist_l:
            continue
        return album, track
    return None


def _structured_payload(album: LibraryAlbum, track: LibraryTrack) -> dict[str, Any]:
    """Build the OpenSubsonic structuredLyrics entry. Unsynced for now (no LRC parsing)."""
    text = track.lyrics or ""
    lines = [{"value": line} for line in text.splitlines()] if text else []
    return {
        "displayArtist": track.artist or album.artist_dir,
        "displayTitle": track.title or track.path.stem,
        "lang": "xxx",  # unknown — future: parse from USLT lang field
        "synced": False,
        "line": lines,
    }


@router.api_route("/getLyrics", methods=["GET", "POST", "HEAD"])
@router.api_route("/getLyrics.view", methods=["GET", "POST", "HEAD"])
async def get_lyrics(
    request: Request,
    artist: str | None = Query(default=None),
    title: str | None = Query(default=None),
) -> dict:
    """Legacy Subsonic lyrics endpoint — fuzzy artist+title lookup."""
    if not artist or not title:
        return envelope("lyrics", {"artist": artist or "", "title": title or "", "value": ""})
    cache = _get_cache(request)
    match = _find_by_artist_title(cache, artist, title)
    if match is None:
        # Spec returns an empty `lyrics` entry rather than an error when the
        # match misses — clients then know there's nothing to display.
        return envelope("lyrics", {"artist": artist, "title": title, "value": ""})
    album, track = match
    return envelope(
        "lyrics",
        {
            "artist": track.artist or album.artist_dir,
            "title": track.title or track.path.stem,
            "value": track.lyrics or "",
        },
    )


@router.api_route("/getLyricsBySongId", methods=["GET", "POST", "HEAD"])
@router.api_route("/getLyricsBySongId.view", methods=["GET", "POST", "HEAD"])
async def get_lyrics_by_song_id(request: Request, id: str = Query(...)) -> dict:
    """OpenSubsonic structured-lyrics endpoint — looks up by stable track ID."""
    cache = _get_cache(request)
    pair = cache.tracks_by_id.get(id)
    if pair is None:
        return error_envelope(70, f"Song not found: {id}")
    album, track = pair
    if not track.lyrics:
        # Empty list rather than error — same convention as getLyrics.
        return envelope("lyricsList", {"structuredLyrics": []})
    return envelope("lyricsList", {"structuredLyrics": [_structured_payload(album, track)]})
