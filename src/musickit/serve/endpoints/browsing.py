"""Subsonic browsing endpoints — artists, albums, songs, indexes, album lists."""

from __future__ import annotations

import random as random_mod
from typing import Any

from fastapi import APIRouter, Query, Request

from musickit.serve.app import envelope, error_envelope
from musickit.serve.index import IndexCache
from musickit.serve.payloads import album_payload, artist_summary, song_payload

router = APIRouter()

_IGNORED_ARTICLES = "The El La Los Las Le Les"


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _index_letter(name: str) -> str:
    """Bucket a name into an A-Z group. Articles + non-letters fold to '#'."""
    stripped = name.lstrip()
    for article in _IGNORED_ARTICLES.split():
        prefix = article + " "
        if stripped.lower().startswith(prefix.lower()):
            stripped = stripped[len(prefix) :]
            break
    if not stripped:
        return "#"
    first = stripped[0].upper()
    return first if first.isalpha() else "#"


@router.api_route("/getArtists", methods=["GET", "POST"])
@router.api_route("/getArtists.view", methods=["GET", "POST"])
async def get_artists(request: Request) -> dict:
    """Alphabetically grouped artist list — the modern (ID3) browse root."""
    cache = _get_cache(request)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ar_id in cache.artists_by_id:
        letter = _index_letter(cache.artist_name_by_id[ar_id])
        buckets.setdefault(letter, []).append(artist_summary(cache, ar_id))
    index = []
    for letter in sorted(buckets):
        artists = sorted(buckets[letter], key=lambda a: str(a["name"]).casefold())
        index.append({"name": letter, "artist": artists})
    return envelope("artists", {"ignoredArticles": _IGNORED_ARTICLES, "index": index})


@router.api_route("/getIndexes", methods=["GET", "POST"])
@router.api_route("/getIndexes.view", methods=["GET", "POST"])
async def get_indexes(request: Request) -> dict:
    """Legacy folder-based browse — same shape as getArtists, different envelope key."""
    cache = _get_cache(request)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ar_id in cache.artists_by_id:
        letter = _index_letter(cache.artist_name_by_id[ar_id])
        buckets.setdefault(letter, []).append(artist_summary(cache, ar_id))
    index = []
    for letter in sorted(buckets):
        artists = sorted(buckets[letter], key=lambda a: str(a["name"]).casefold())
        index.append({"name": letter, "artist": artists})
    return envelope(
        "indexes",
        {"ignoredArticles": _IGNORED_ARTICLES, "lastModified": 0, "index": index},
    )


@router.api_route("/getArtist", methods=["GET", "POST"])
@router.api_route("/getArtist.view", methods=["GET", "POST"])
async def get_artist(request: Request, id: str = Query(...)) -> dict:
    """Albums for one artist."""
    cache = _get_cache(request)
    albums = cache.artists_by_id.get(id)
    if albums is None:
        return error_envelope(70, f"Artist not found: {id}")
    sorted_albums = sorted(albums, key=lambda a: (a.tag_year or "9999", (a.tag_album or a.album_dir).casefold()))
    return envelope(
        "artist",
        {
            "id": id,
            "name": cache.artist_name_by_id[id],
            "albumCount": len(sorted_albums),
            "coverArt": id,
            "album": [album_payload(a, with_songs=False) for a in sorted_albums],
        },
    )


@router.api_route("/getAlbum", methods=["GET", "POST"])
@router.api_route("/getAlbum.view", methods=["GET", "POST"])
async def get_album(request: Request, id: str = Query(...)) -> dict:
    """One album with its tracks."""
    cache = _get_cache(request)
    album = cache.albums_by_id.get(id)
    if album is None:
        return error_envelope(70, f"Album not found: {id}")
    return envelope("album", album_payload(album, with_songs=True))


@router.api_route("/getSong", methods=["GET", "POST"])
@router.api_route("/getSong.view", methods=["GET", "POST"])
async def get_song(request: Request, id: str = Query(...)) -> dict:
    """One track."""
    cache = _get_cache(request)
    pair = cache.tracks_by_id.get(id)
    if pair is None:
        return error_envelope(70, f"Song not found: {id}")
    album, track = pair
    return envelope("song", song_payload(album, track))


@router.api_route("/getAlbumList2", methods=["GET", "POST"])
@router.api_route("/getAlbumList2.view", methods=["GET", "POST"])
async def get_album_list2(  # noqa: PLR0912 — Subsonic's `type` enum has many cases
    request: Request,
    type: str = Query(default="alphabeticalByName"),
    size: int = Query(default=10, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    fromYear: int | None = Query(default=None),
    toYear: int | None = Query(default=None),
    genre: str | None = Query(default=None),
) -> dict:
    """Flat album list for browse-screens (NEW / RANDOM / A-Z / By Year / By Genre)."""
    cache = _get_cache(request)
    albums = list(cache.albums_by_id.values())

    if type == "random":
        random_mod.shuffle(albums)
    elif type == "byYear":
        if fromYear is None or toYear is None:
            return error_envelope(10, "byYear requires fromYear and toYear")
        lo, hi = (fromYear, toYear) if fromYear <= toYear else (toYear, fromYear)
        albums = [a for a in albums if a.tag_year and lo <= _year_int(a.tag_year) <= hi]
        # Subsonic sorts byYear chronologically (ascending fromYear → toYear, descending if reversed).
        descending = fromYear > toYear
        albums.sort(key=lambda a: _year_int(a.tag_year or "0"), reverse=descending)
    elif type == "byGenre":
        if not genre:
            return error_envelope(10, "byGenre requires genre")
        target = genre.casefold()
        albums = [a for a in albums if any((t.album_artist or "").casefold() == target for t in a.tracks)]
        albums.sort(key=lambda a: (a.tag_album or a.album_dir).casefold())
    elif type == "alphabeticalByArtist":
        albums.sort(key=lambda a: (a.artist_dir.casefold(), (a.tag_album or a.album_dir).casefold()))
    else:
        # alphabeticalByName + every unsupported type (newest, highest, frequent,
        # recent, starred) all fall back to alphabetical-by-name. Cleaner than
        # returning an error for clients that reach for "newest" by default.
        albums.sort(key=lambda a: (a.tag_album or a.album_dir).casefold())

    page = albums[offset : offset + size]
    return envelope("albumList2", {"album": [album_payload(a, with_songs=False) for a in page]})


def _year_int(year: str) -> int:
    try:
        return int(year)
    except ValueError:
        return 0
