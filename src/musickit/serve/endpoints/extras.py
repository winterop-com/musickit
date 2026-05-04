"""Stub endpoints — keep clients quiet on features we don't track.

These return well-formed empty responses for things musickit doesn't
actually store (scrobbles, stars/favourites, playlists, artist bios).
Implementing them as no-ops is much friendlier than letting clients
log 404s on every play.

Real implementations would back these with persistent state; for now,
the goal is just to make the API surface complete enough that:
  - play:Sub stops reporting "scrobble failed" errors
  - artist screens render (even with empty bios) instead of erroring
  - "Random songs" / "Starred" tabs in clients show empty lists
    instead of error toasts
"""

from __future__ import annotations

import random as random_mod
from typing import Any

from fastapi import APIRouter, Query, Request

from musickit.serve.app import envelope, error_envelope
from musickit.serve.index import IndexCache
from musickit.serve.payloads import album_payload, song_payload

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Scrobble — clients send this after every play. We don't track plays.
# ---------------------------------------------------------------------------


@router.api_route("/scrobble", methods=["GET", "POST", "HEAD"])
@router.api_route("/scrobble.view", methods=["GET", "POST", "HEAD"])
async def scrobble() -> dict:
    """No-op — accept and discard. Returning ok keeps client logs clean."""
    return envelope()


# ---------------------------------------------------------------------------
# Artist info — biography + similar artists. We don't have either source.
# ---------------------------------------------------------------------------


def _empty_artist_info(_artist_id: str) -> dict[str, Any]:
    return {
        "biography": "",
        "musicBrainzId": "",
        "lastFmUrl": "",
        "smallImageUrl": "",
        "mediumImageUrl": "",
        "largeImageUrl": "",
        "similarArtist": [],
    }


@router.api_route("/getArtistInfo", methods=["GET", "POST", "HEAD"])
@router.api_route("/getArtistInfo.view", methods=["GET", "POST", "HEAD"])
async def get_artist_info(id: str = Query(...)) -> dict:
    """Empty artist info (legacy v1 endpoint) — no bio/similar-artist data tracked."""
    return envelope("artistInfo", _empty_artist_info(id))


@router.api_route("/getArtistInfo2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getArtistInfo2.view", methods=["GET", "POST", "HEAD"])
async def get_artist_info2(id: str = Query(...)) -> dict:
    """Empty artist info (modern v3 endpoint)."""
    return envelope("artistInfo2", _empty_artist_info(id))


# ---------------------------------------------------------------------------
# getMusicDirectory — legacy folder browse. Routes by ID prefix to the
# corresponding modern endpoint's data.
# ---------------------------------------------------------------------------


@router.api_route("/getMusicDirectory", methods=["GET", "POST", "HEAD"])
@router.api_route("/getMusicDirectory.view", methods=["GET", "POST", "HEAD"])
async def get_music_directory(request: Request, id: str = Query(...)) -> dict:
    """Legacy folder browse. `ar_*` → albums; `al_*` → tracks."""
    cache = _get_cache(request)
    if id.startswith("ar_"):
        albums = cache.artists_by_id.get(id)
        if albums is None:
            return error_envelope(70, f"Directory not found: {id}")
        sorted_albums = sorted(albums, key=lambda a: (a.tag_year or "9999", (a.tag_album or a.album_dir).casefold()))
        return envelope(
            "directory",
            {
                "id": id,
                "name": cache.artist_name_by_id[id],
                "child": [{**album_payload(a, with_songs=False), "isDir": True} for a in sorted_albums],
            },
        )
    if id.startswith("al_"):
        album = cache.albums_by_id.get(id)
        if album is None:
            return error_envelope(70, f"Directory not found: {id}")
        return envelope(
            "directory",
            {
                "id": id,
                "name": album.tag_album or album.album_dir,
                "parent": next(iter([k for k, v in cache.artists_by_id.items() if album in v]), None),
                "child": [song_payload(album, t) for t in album.tracks],
            },
        )
    return error_envelope(70, f"Unknown directory id format: {id}")


# ---------------------------------------------------------------------------
# getRandomSongs — used by clients' home screens / "shuffle all" buttons.
# ---------------------------------------------------------------------------


@router.api_route("/getRandomSongs", methods=["GET", "POST", "HEAD"])
@router.api_route("/getRandomSongs.view", methods=["GET", "POST", "HEAD"])
async def get_random_songs(
    request: Request,
    size: int = Query(default=10, ge=1, le=500),
    fromYear: int | None = Query(default=None),
    toYear: int | None = Query(default=None),
) -> dict:
    """Return up to `size` random songs from the cache."""
    cache = _get_cache(request)
    pool = list(cache.tracks_by_id.values())
    if fromYear is not None and toYear is not None:
        lo, hi = (fromYear, toYear) if fromYear <= toYear else (toYear, fromYear)
        pool = [(a, t) for a, t in pool if t.year and t.year.isdigit() and lo <= int(t.year) <= hi]
    if not pool:
        return envelope("randomSongs", {"song": []})
    sample = random_mod.sample(pool, min(size, len(pool)))
    return envelope("randomSongs", {"song": [song_payload(album, track) for album, track in sample]})


# ---------------------------------------------------------------------------
# Stars / favourites — return empty + accept no-op writes.
# ---------------------------------------------------------------------------


@router.api_route("/getStarred", methods=["GET", "POST", "HEAD"])
@router.api_route("/getStarred.view", methods=["GET", "POST", "HEAD"])
async def get_starred() -> dict:
    """No starring support — return an empty starred set."""
    return envelope("starred", {"artist": [], "album": [], "song": []})


@router.api_route("/getStarred2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getStarred2.view", methods=["GET", "POST", "HEAD"])
async def get_starred2() -> dict:
    """No starring support (v2)."""
    return envelope("starred2", {"artist": [], "album": [], "song": []})


@router.api_route("/star", methods=["GET", "POST", "HEAD"])
@router.api_route("/star.view", methods=["GET", "POST", "HEAD"])
async def star() -> dict:
    """No-op: starring not persisted."""
    return envelope()


@router.api_route("/unstar", methods=["GET", "POST", "HEAD"])
@router.api_route("/unstar.view", methods=["GET", "POST", "HEAD"])
async def unstar() -> dict:
    """No-op: unstarring not persisted (since nothing is ever starred)."""
    return envelope()


# ---------------------------------------------------------------------------
# Playlists — return empty list. Read-only support is a future addition.
# ---------------------------------------------------------------------------


@router.api_route("/getPlaylists", methods=["GET", "POST", "HEAD"])
@router.api_route("/getPlaylists.view", methods=["GET", "POST", "HEAD"])
async def get_playlists() -> dict:
    """No playlist support yet — return empty list."""
    return envelope("playlists", {"playlist": []})


# ---------------------------------------------------------------------------
# Users — single-user server. Feishin / Supersonic / others probe getUser
# right after login to learn the role flags.
# ---------------------------------------------------------------------------


def _user_payload(username: str) -> dict[str, Any]:
    """All-roles-true. Single-user server, the user owns the box."""
    return {
        "username": username,
        "email": f"{username}@musickit.local",
        "scrobblingEnabled": True,
        "adminRole": True,
        "settingsRole": True,
        "downloadRole": True,
        "uploadRole": False,
        "playlistRole": True,
        "coverArtRole": True,
        "commentRole": False,
        "podcastRole": False,
        "streamRole": True,
        "jukeboxRole": False,
        "shareRole": False,
        "videoConversionRole": False,
        "folder": [1],
    }


@router.api_route("/getUser", methods=["GET", "POST", "HEAD"])
@router.api_route("/getUser.view", methods=["GET", "POST", "HEAD"])
async def get_user(request: Request, username: str | None = Query(default=None)) -> dict:
    """Return user details. The single configured user has every role."""
    cfg = request.app.state.cfg
    target = username or cfg.username
    if target != cfg.username:
        return error_envelope(70, f"User not found: {target}")
    return envelope("user", _user_payload(cfg.username))


@router.api_route("/getUsers", methods=["GET", "POST", "HEAD"])
@router.api_route("/getUsers.view", methods=["GET", "POST", "HEAD"])
async def get_users(request: Request) -> dict:
    """Return all users — just the one we host."""
    cfg = request.app.state.cfg
    return envelope("users", {"user": [_user_payload(cfg.username)]})


# ---------------------------------------------------------------------------
# OpenSubsonic discovery — clients ask which extensions we support.
# We don't implement any optional extensions yet, so return an empty list.
# The `openSubsonic: true` flag in our standard envelope already advertises
# basic OpenSubsonic compliance.
# ---------------------------------------------------------------------------


@router.api_route("/getOpenSubsonicExtensions", methods=["GET", "POST", "HEAD"])
@router.api_route("/getOpenSubsonicExtensions.view", methods=["GET", "POST", "HEAD"])
async def get_open_subsonic_extensions() -> dict:
    """Advertise the OpenSubsonic extensions we actually support.

    Clients use this to light up extra UI:
    - `formPost` lets them submit credentials in a POST body instead of the
      query string. Implemented by `PostFormToQueryMiddleware`.
    - `transcodeOffset` lets them seek mid-transcode by passing
      `?timeOffset=N` to `/stream`. Implemented by ffmpeg `-ss N` in
      `_transcode_response`.
    """
    return envelope(
        "openSubsonicExtensions",
        [
            {"name": "formPost", "versions": [1]},
            {"name": "transcodeOffset", "versions": [1]},
            {"name": "multipleGenres", "versions": [1]},
            {"name": "songLyrics", "versions": [1]},
        ],
    )


# ---------------------------------------------------------------------------
# Genres — counted from the library scan. `getGenres` returns one entry per
# distinct genre with songCount + albumCount; clients render a "Genres" tab.
# ---------------------------------------------------------------------------


@router.api_route("/getGenres", methods=["GET", "POST", "HEAD"])
@router.api_route("/getGenres.view", methods=["GET", "POST", "HEAD"])
async def get_genres(request: Request) -> dict:
    """Distinct genres + per-genre song / album counts."""
    cache = _get_cache(request)
    song_counts: dict[str, int] = {}
    album_genres: dict[str, set[str]] = {}  # genre → set of album IDs
    for album_id_str, album in cache.albums_by_id.items():
        seen_in_album: set[str] = set()
        for track in album.tracks:
            name = track.genre or album.tag_genre
            if not name:
                continue
            song_counts[name] = song_counts.get(name, 0) + 1
            seen_in_album.add(name)
        # Also count album-level tag_genre even when no track had it explicitly.
        if album.tag_genre:
            seen_in_album.add(album.tag_genre)
        for name in seen_in_album:
            album_genres.setdefault(name, set()).add(album_id_str)
    payload = [
        {"value": name, "songCount": song_counts.get(name, 0), "albumCount": len(albums)}
        for name, albums in sorted(album_genres.items(), key=lambda kv: kv[0].casefold())
    ]
    return envelope("genres", {"genre": payload})
