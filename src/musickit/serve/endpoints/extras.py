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
from musickit.serve.config import ServeConfig
from musickit.serve.index import IndexCache
from musickit.serve.payloads import album_payload, artist_summary, song_payload
from musickit.serve.scrobble import ScrobbleDispatcher, ScrobbleEvent
from musickit.serve.stars import StarStore

router = APIRouter()


def _get_cache(request: Request) -> IndexCache:
    return request.app.state.cache  # type: ignore[no-any-return]


def _get_stars(request: Request) -> StarStore:
    return request.app.state.stars  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Scrobble — clients send this after every play. We don't track plays.
# ---------------------------------------------------------------------------


@router.api_route("/scrobble", methods=["GET", "POST", "HEAD"])
@router.api_route("/scrobble.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def scrobble(
    request: Request,
    id: str | None = Query(default=None),
    time: int | None = Query(default=None),
    submission: bool = Query(default=True),
    u: str | None = Query(default=None),
) -> dict:
    """Forward the play event to configured webhook / MQTT targets, or no-op.

    The Subsonic spec defines `submission=false` as the "now playing"
    probe (fired at track start) and `submission=true` as the "I
    finished playing this" event (fired at track end / threshold).
    Forwarders default to true-only; flip `[scrobble].include_now_playing`
    to receive both.

    `time` is the start time of playback in unix-millis (per the spec).
    Falls back to "now" when the client doesn't send it.
    """
    del time  # client-supplied wall-clock; we use server-side `played_at` for stability
    cache = _get_cache(request)
    dispatcher: ScrobbleDispatcher = request.app.state.scrobble

    # No id → invalid request shape; still ack so the client doesn't bark.
    if not id:
        return envelope()
    # Resolve track for the payload. Unknown ID → still ack; just don't forward.
    pair = cache.tracks_by_id.get(id)
    if pair is None:
        return envelope()

    album, track = pair
    cfg: ServeConfig = request.app.state.cfg
    user = u or cfg.username

    from datetime import UTC, datetime

    event = ScrobbleEvent(
        user=user,
        track_id=id,
        title=track.title or track.path.stem,
        artist=track.artist or album.artist_dir,
        album=album.tag_album or album.album_dir,
        duration_s=float(track.duration_s or 0.0),
        played_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        submission=bool(submission),
    )
    dispatcher.dispatch(event)
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
@router.api_route("/getArtistInfo.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_artist_info(id: str = Query(...)) -> dict:
    """Empty artist info (legacy v1 endpoint) — no bio/similar-artist data tracked."""
    return envelope("artistInfo", _empty_artist_info(id))


@router.api_route("/getArtistInfo2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getArtistInfo2.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_artist_info2(id: str = Query(...)) -> dict:
    """Empty artist info (modern v3 endpoint)."""
    return envelope("artistInfo2", _empty_artist_info(id))


# ---------------------------------------------------------------------------
# getMusicDirectory — legacy folder browse. Routes by ID prefix to the
# corresponding modern endpoint's data.
# ---------------------------------------------------------------------------


@router.api_route("/getMusicDirectory", methods=["GET", "POST", "HEAD"])
@router.api_route("/getMusicDirectory.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
@router.api_route("/getRandomSongs.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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


def _build_starred_payload(cache: IndexCache, stars: StarStore) -> dict[str, Any]:
    """Categorised lists of currently-starred entries.

    Resolves each starred ID against the cache and silently filters out
    "ghost" entries (file deleted / renamed since starring) — the
    client should never see broken rows. To clean up the underlying
    file, call `stars.prune(...)` separately.
    """
    artists: list[dict[str, Any]] = []
    albums: list[dict[str, Any]] = []
    songs: list[dict[str, Any]] = []

    for sid, ts in stars.all_ids().items():
        if sid.startswith("ar_") and sid in cache.artists_by_id:
            entry = artist_summary(cache, sid)
            entry["starred"] = ts
            artists.append(entry)
        elif sid.startswith("al_") and sid in cache.albums_by_id:
            entry = album_payload(cache.albums_by_id[sid], with_songs=False)
            entry["starred"] = ts
            albums.append(entry)
        elif sid.startswith("tr_") and sid in cache.tracks_by_id:
            album, track = cache.tracks_by_id[sid]
            entry = song_payload(album, track)
            entry["starred"] = ts
            songs.append(entry)

    # Sort within each kind by `starred` desc so the most-recent show up first.
    # Clients (Symfonium / Amperfy) don't sort the response themselves.
    artists.sort(key=lambda d: d.get("starred", ""), reverse=True)
    albums.sort(key=lambda d: d.get("starred", ""), reverse=True)
    songs.sort(key=lambda d: d.get("starred", ""), reverse=True)
    return {"artist": artists, "album": albums, "song": songs}


@router.api_route("/getStarred", methods=["GET", "POST", "HEAD"])
@router.api_route("/getStarred.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_starred(request: Request) -> dict:
    """Return artists / albums / songs currently starred by the user."""
    return envelope("starred", _build_starred_payload(_get_cache(request), _get_stars(request)))


@router.api_route("/getStarred2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getStarred2.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_starred2(request: Request) -> dict:
    """v2 of getStarred — same payload shape; the wrapper key differs."""
    return envelope("starred2", _build_starred_payload(_get_cache(request), _get_stars(request)))


@router.api_route("/star", methods=["GET", "POST", "HEAD"])
@router.api_route("/star.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def star(
    request: Request,
    id: str | None = Query(default=None),
    albumId: str | None = Query(default=None),  # noqa: N803 — Subsonic spec uses camelCase
    artistId: str | None = Query(default=None),  # noqa: N803
) -> dict:
    """Star one or more entities. Subsonic accepts any combination of `id`, `albumId`, `artistId`.

    The `id` parameter accepts artist / album / track IDs (clients tend
    to pass through whatever is currently selected); `albumId` and
    `artistId` are explicit kind-typed variants. We star every ID
    provided — unknown IDs are silently ignored so a stale client
    request doesn't 500.
    """
    stars = _get_stars(request)
    cache = _get_cache(request)
    for sid in _collect_ids(id, albumId, artistId):
        if _id_resolves(cache, sid):
            stars.add(sid)
    return envelope()


@router.api_route("/unstar", methods=["GET", "POST", "HEAD"])
@router.api_route("/unstar.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def unstar(
    request: Request,
    id: str | None = Query(default=None),
    albumId: str | None = Query(default=None),  # noqa: N803
    artistId: str | None = Query(default=None),  # noqa: N803
) -> dict:
    """Unstar — symmetric with /star. Idempotent on already-unstarred IDs."""
    stars = _get_stars(request)
    for sid in _collect_ids(id, albumId, artistId):
        stars.remove(sid)
    return envelope()


def _collect_ids(*sources: str | None) -> list[str]:
    """Collect Subsonic IDs from /star query params, dedup, drop blanks."""
    out: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if not src:
            continue
        # Subsonic clients sometimes send comma-separated lists for `id`.
        for sid in src.split(","):
            sid = sid.strip()
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
    return out


def _id_resolves(cache: IndexCache, sid: str) -> bool:
    """True iff `sid` matches a current artist / album / track in the cache."""
    if sid.startswith("ar_"):
        return sid in cache.artists_by_id
    if sid.startswith("al_"):
        return sid in cache.albums_by_id
    if sid.startswith("tr_"):
        return sid in cache.tracks_by_id
    return False


# ---------------------------------------------------------------------------
# Playlists — return empty list. Read-only support is a future addition.
# ---------------------------------------------------------------------------


@router.api_route("/getPlaylists", methods=["GET", "POST", "HEAD"])
@router.api_route("/getPlaylists.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
@router.api_route("/getUser.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_user(request: Request, username: str | None = Query(default=None)) -> dict:
    """Return user details. The single configured user has every role."""
    cfg = request.app.state.cfg
    target = username or cfg.username
    if target != cfg.username:
        return error_envelope(70, f"User not found: {target}")
    return envelope("user", _user_payload(cfg.username))


@router.api_route("/getUsers", methods=["GET", "POST", "HEAD"])
@router.api_route("/getUsers.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
@router.api_route("/getOpenSubsonicExtensions.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
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
@router.api_route("/getGenres.view", methods=["GET", "POST", "HEAD"], include_in_schema=False)
async def get_genres(request: Request) -> dict:
    """Distinct genres + per-genre song / album counts.

    Honours the OpenSubsonic `multipleGenres` extension: a track tagged
    `genres=["Rock", "Indie"]` contributes one song to BOTH counts, and
    its album is counted under both. Falls back to the legacy single
    `track.genre` (and album-level `tag_genre`) when the multi-list is
    absent.
    """
    cache = _get_cache(request)
    song_counts: dict[str, int] = {}
    album_genres: dict[str, set[str]] = {}  # genre → set of album IDs
    for album_id_str, album in cache.albums_by_id.items():
        seen_in_album: set[str] = set()
        for track in album.tracks:
            track_genres = list(track.genres) if track.genres else []
            if not track_genres and track.genre:
                track_genres = [track.genre]
            if not track_genres and album.tag_genre:
                track_genres = [album.tag_genre]
            for name in track_genres:
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
