"""Subsonic `search3` (and legacy `search2` alias) — substring across the index."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from musickit.serve.app import envelope
from musickit.serve.index import IndexCache
from musickit.serve.payloads import album_payload, artist_summary, song_payload

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _matches(needle: str, haystack: str) -> bool:
    """Multi-token AND match on casefolded strings — clients send `query=foo bar`."""
    return all(token in haystack for token in needle.split() if token)


def _search(
    cache: IndexCache,
    *,
    query: str,
    artist_count: int,
    album_count: int,
    song_count: int,
    artist_offset: int = 0,
    album_offset: int = 0,
    song_offset: int = 0,
) -> dict[str, list[dict]]:
    """Pure search — used by both `search3` and `search2` so neither calls the other endpoint."""
    needle = query.casefold().strip()
    artists: list[dict] = []
    albums: list[dict] = []
    songs: list[dict] = []
    if not needle:
        return {"artist": artists, "album": albums, "song": songs}

    if artist_count > 0:
        for ar_id, name in cache.artist_name_by_id.items():
            if _matches(needle, name.casefold()):
                artists.append(artist_summary(cache, ar_id))
        artists.sort(key=lambda a: str(a["name"]).casefold())
        artists = artists[artist_offset : artist_offset + artist_count]

    if album_count > 0:
        for album in cache.albums_by_id.values():
            target = (album.tag_album or album.album_dir).casefold()
            if _matches(needle, target):
                albums.append(album_payload(album, with_songs=False))
        albums.sort(key=lambda a: str(a["name"]).casefold())
        albums = albums[album_offset : album_offset + album_count]

    if song_count > 0:
        for album, track in cache.tracks_by_id.values():
            title = (track.title or track.path.stem).casefold()
            if _matches(needle, title):
                songs.append(song_payload(album, track))
        songs.sort(key=lambda s: str(s["title"]).casefold())
        songs = songs[song_offset : song_offset + song_count]

    return {"artist": artists, "album": albums, "song": songs}


@router.api_route("/search3", methods=["GET", "POST"])
@router.api_route("/search3.view", methods=["GET", "POST"])
async def search3(
    request: Request,
    query: str = Query(...),
    artistCount: int = Query(default=20, ge=0, le=500),
    albumCount: int = Query(default=20, ge=0, le=500),
    songCount: int = Query(default=20, ge=0, le=500),
    artistOffset: int = Query(default=0, ge=0),
    albumOffset: int = Query(default=0, ge=0),
    songOffset: int = Query(default=0, ge=0),
) -> dict:
    """Modern (ID3) search across the cached index. Multi-token AND, case-insensitive."""
    result = _search(
        _get_cache(request),
        query=query,
        artist_count=artistCount,
        album_count=albumCount,
        song_count=songCount,
        artist_offset=artistOffset,
        album_offset=albumOffset,
        song_offset=songOffset,
    )
    return envelope("searchResult3", result)


@router.api_route("/search2", methods=["GET", "POST"])
@router.api_route("/search2.view", methods=["GET", "POST"])
async def search2(
    request: Request,
    query: str = Query(...),
    artistCount: int = Query(default=20, ge=0, le=500),
    albumCount: int = Query(default=20, ge=0, le=500),
    songCount: int = Query(default=20, ge=0, le=500),
) -> dict:
    """Legacy (folder) search — same matching logic, `searchResult2` envelope key."""
    result = _search(
        _get_cache(request),
        query=query,
        artist_count=artistCount,
        album_count=albumCount,
        song_count=songCount,
    )
    return envelope("searchResult2", result)
