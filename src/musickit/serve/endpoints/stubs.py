"""Empty-body stubs for Subsonic endpoints we don't implement.

Every Subsonic client (Symfonium, Amperfy, Feishin, play:Sub, DSub) probes
a long tail of endpoints whether the user uses the feature or not — most
notably podcasts, bookmarks, play-queue sync, shares, and similar-track
discovery. With no handler the endpoint 404s, the client logs the error,
some clients retry persistently, and a few even refuse to load other
parts of the UI until "their" endpoint stops failing.

Each handler returns a well-formed `subsonic-response` envelope with an
empty payload of the right shape. Clients are happy, our error logs are
clean, and the door is open to upgrade any of these to a real
implementation later — the route signature is stable.

Shapes follow https://opensubsonic.netlify.app/docs/api-reference/ with
the leading `subsonic-response` wrapper applied by `envelope()` here.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from musickit.serve.app import envelope

router = APIRouter()


# ---------------------------------------------------------------------------
# Podcasts
# ---------------------------------------------------------------------------


@router.api_route("/getPodcasts", methods=["GET", "POST", "HEAD"])
@router.api_route("/getPodcasts.view", methods=["GET", "POST", "HEAD"])
async def get_podcasts() -> dict:
    """Empty podcast channel list — clients probe this on launch."""
    return envelope("podcasts", {"channel": []})


@router.api_route("/getNewestPodcasts", methods=["GET", "POST", "HEAD"])
@router.api_route("/getNewestPodcasts.view", methods=["GET", "POST", "HEAD"])
async def get_newest_podcasts() -> dict:
    """Empty newest-episodes list."""
    return envelope("newestPodcasts", {"episode": []})


@router.api_route("/refreshPodcasts", methods=["GET", "POST", "HEAD"])
@router.api_route("/refreshPodcasts.view", methods=["GET", "POST", "HEAD"])
async def refresh_podcasts() -> dict:
    """No-op — we don't store podcast subscriptions."""
    return envelope()


@router.api_route("/createPodcastChannel", methods=["GET", "POST", "HEAD"])
@router.api_route("/createPodcastChannel.view", methods=["GET", "POST", "HEAD"])
async def create_podcast_channel() -> dict:
    """Accept-and-discard — we don't store podcast subscriptions."""
    return envelope()


@router.api_route("/deletePodcastChannel", methods=["GET", "POST", "HEAD"])
@router.api_route("/deletePodcastChannel.view", methods=["GET", "POST", "HEAD"])
async def delete_podcast_channel() -> dict:
    """No-op — we don't store podcast subscriptions."""
    return envelope()


@router.api_route("/deletePodcastEpisode", methods=["GET", "POST", "HEAD"])
@router.api_route("/deletePodcastEpisode.view", methods=["GET", "POST", "HEAD"])
async def delete_podcast_episode() -> dict:
    """No-op — we don't store podcast episodes."""
    return envelope()


@router.api_route("/downloadPodcastEpisode", methods=["GET", "POST", "HEAD"])
@router.api_route("/downloadPodcastEpisode.view", methods=["GET", "POST", "HEAD"])
async def download_podcast_episode() -> dict:
    """No-op — we don't fetch podcast media."""
    return envelope()


# ---------------------------------------------------------------------------
# Bookmarks (mid-track resume position)
# ---------------------------------------------------------------------------


@router.api_route("/getBookmarks", methods=["GET", "POST", "HEAD"])
@router.api_route("/getBookmarks.view", methods=["GET", "POST", "HEAD"])
async def get_bookmarks() -> dict:
    """Empty bookmark list."""
    return envelope("bookmarks", {"bookmark": []})


@router.api_route("/createBookmark", methods=["GET", "POST", "HEAD"])
@router.api_route("/createBookmark.view", methods=["GET", "POST", "HEAD"])
async def create_bookmark() -> dict:
    """Accept-and-discard — bookmarks aren't persisted server-side."""
    return envelope()


@router.api_route("/deleteBookmark", methods=["GET", "POST", "HEAD"])
@router.api_route("/deleteBookmark.view", methods=["GET", "POST", "HEAD"])
async def delete_bookmark() -> dict:
    """No-op — bookmarks aren't persisted server-side."""
    return envelope()


# ---------------------------------------------------------------------------
# Play queue (cross-device sync)
# ---------------------------------------------------------------------------


@router.api_route("/getPlayQueue", methods=["GET", "POST", "HEAD"])
@router.api_route("/getPlayQueue.view", methods=["GET", "POST", "HEAD"])
async def get_play_queue() -> dict:
    """Empty queue — clients fall back to local-only state."""
    return envelope()


@router.api_route("/savePlayQueue", methods=["GET", "POST", "HEAD"])
@router.api_route("/savePlayQueue.view", methods=["GET", "POST", "HEAD"])
async def save_play_queue() -> dict:
    """Accept-and-discard — we don't sync queue across devices."""
    return envelope()


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------


@router.api_route("/getShares", methods=["GET", "POST", "HEAD"])
@router.api_route("/getShares.view", methods=["GET", "POST", "HEAD"])
async def get_shares() -> dict:
    """Empty share list — sharing isn't a single-user-server concept."""
    return envelope("shares", {"share": []})


@router.api_route("/createShare", methods=["GET", "POST", "HEAD"])
@router.api_route("/createShare.view", methods=["GET", "POST", "HEAD"])
async def create_share() -> dict:
    """Accept-and-discard — shares aren't persisted."""
    return envelope("shares", {"share": []})


@router.api_route("/updateShare", methods=["GET", "POST", "HEAD"])
@router.api_route("/updateShare.view", methods=["GET", "POST", "HEAD"])
async def update_share() -> dict:
    """No-op — shares aren't persisted."""
    return envelope()


@router.api_route("/deleteShare", methods=["GET", "POST", "HEAD"])
@router.api_route("/deleteShare.view", methods=["GET", "POST", "HEAD"])
async def delete_share() -> dict:
    """No-op — shares aren't persisted."""
    return envelope()


# ---------------------------------------------------------------------------
# Internet radio stations
#
# musickit's TUI has its own radio.toml-driven stations; the Subsonic
# spec models internet radio differently (per-server stored stations).
# Returning empty here keeps clients quiet without conflating the two.
# ---------------------------------------------------------------------------


@router.api_route("/getInternetRadioStations", methods=["GET", "POST", "HEAD"])
@router.api_route("/getInternetRadioStations.view", methods=["GET", "POST", "HEAD"])
async def get_internet_radio_stations() -> dict:
    """Empty list — we don't surface TUI radio stations through the spec endpoint."""
    return envelope("internetRadioStations", {"internetRadioStation": []})


@router.api_route("/createInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/createInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def create_internet_radio_station() -> dict:
    """No-op — radio stations live in the TUI's local config."""
    return envelope()


@router.api_route("/updateInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/updateInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def update_internet_radio_station() -> dict:
    """No-op — radio stations live in the TUI's local config."""
    return envelope()


@router.api_route("/deleteInternetRadioStation", methods=["GET", "POST", "HEAD"])
@router.api_route("/deleteInternetRadioStation.view", methods=["GET", "POST", "HEAD"])
async def delete_internet_radio_station() -> dict:
    """No-op — radio stations live in the TUI's local config."""
    return envelope()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.api_route("/getChatMessages", methods=["GET", "POST", "HEAD"])
@router.api_route("/getChatMessages.view", methods=["GET", "POST", "HEAD"])
async def get_chat_messages() -> dict:
    """Empty chat — single-user serve has no chat surface."""
    return envelope("chatMessages", {"chatMessage": []})


@router.api_route("/addChatMessage", methods=["GET", "POST", "HEAD"])
@router.api_route("/addChatMessage.view", methods=["GET", "POST", "HEAD"])
async def add_chat_message() -> dict:
    """Accept-and-discard — chat is not persisted."""
    return envelope()


# ---------------------------------------------------------------------------
# Jukebox (local audio output controlled by API)
# ---------------------------------------------------------------------------


@router.api_route("/jukeboxControl", methods=["GET", "POST", "HEAD"])
@router.api_route("/jukeboxControl.view", methods=["GET", "POST", "HEAD"])
async def jukebox_control() -> dict:
    """Idle jukebox status — we don't run server-side audio output."""
    return envelope(
        "jukeboxStatus",
        {"currentIndex": 0, "playing": False, "gain": 1.0, "position": 0},
    )


# ---------------------------------------------------------------------------
# Discovery / similarity
# ---------------------------------------------------------------------------


@router.api_route("/getSimilarSongs", methods=["GET", "POST", "HEAD"])
@router.api_route("/getSimilarSongs.view", methods=["GET", "POST", "HEAD"])
async def get_similar_songs(id: str = Query(default="")) -> dict:
    """Empty similar-songs list — no acoustic-fingerprint similarity in v1."""
    del id
    return envelope("similarSongs", {"song": []})


@router.api_route("/getSimilarSongs2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getSimilarSongs2.view", methods=["GET", "POST", "HEAD"])
async def get_similar_songs2(id: str = Query(default="")) -> dict:
    """Empty similar-songs list (modern endpoint variant)."""
    del id
    return envelope("similarSongs2", {"song": []})


@router.api_route("/getTopSongs", methods=["GET", "POST", "HEAD"])
@router.api_route("/getTopSongs.view", methods=["GET", "POST", "HEAD"])
async def get_top_songs(artist: str = Query(default="")) -> dict:
    """Empty top-songs list — we don't track plays globally."""
    del artist
    return envelope("topSongs", {"song": []})


# ---------------------------------------------------------------------------
# Album info
# ---------------------------------------------------------------------------


_EMPTY_ALBUM_INFO = {
    "notes": "",
    "musicBrainzId": "",
    "lastFmUrl": "",
    "smallImageUrl": "",
    "mediumImageUrl": "",
    "largeImageUrl": "",
}


@router.api_route("/getAlbumInfo", methods=["GET", "POST", "HEAD"])
@router.api_route("/getAlbumInfo.view", methods=["GET", "POST", "HEAD"])
async def get_album_info(id: str = Query(default="")) -> dict:
    """Empty album info — no notes / external image source tracked."""
    del id
    return envelope("albumInfo", dict(_EMPTY_ALBUM_INFO))


@router.api_route("/getAlbumInfo2", methods=["GET", "POST", "HEAD"])
@router.api_route("/getAlbumInfo2.view", methods=["GET", "POST", "HEAD"])
async def get_album_info2(id: str = Query(default="")) -> dict:
    """Empty album info (modern endpoint variant)."""
    del id
    return envelope("albumInfo", dict(_EMPTY_ALBUM_INFO))


# ---------------------------------------------------------------------------
# Now playing — multi-user feature; single-user musickit is always empty
# ---------------------------------------------------------------------------


@router.api_route("/getNowPlaying", methods=["GET", "POST", "HEAD"])
@router.api_route("/getNowPlaying.view", methods=["GET", "POST", "HEAD"])
async def get_now_playing() -> dict:
    """Empty now-playing list — single-user serve has nobody else listening."""
    return envelope("nowPlaying", {"entry": []})


# ---------------------------------------------------------------------------
# Per-track rating
# ---------------------------------------------------------------------------


@router.api_route("/setRating", methods=["GET", "POST", "HEAD"])
@router.api_route("/setRating.view", methods=["GET", "POST", "HEAD"])
async def set_rating(id: str = Query(default=""), rating: int = Query(default=0)) -> dict:
    """Accept-and-discard — ratings aren't persisted."""
    del id, rating
    return envelope()


# ---------------------------------------------------------------------------
# User management — single-user serve, accept-and-discard
# ---------------------------------------------------------------------------


@router.api_route("/changePassword", methods=["GET", "POST", "HEAD"])
@router.api_route("/changePassword.view", methods=["GET", "POST", "HEAD"])
async def change_password() -> dict:
    """No-op — credentials live in serve.toml, not server-side state."""
    return envelope()


@router.api_route("/createUser", methods=["GET", "POST", "HEAD"])
@router.api_route("/createUser.view", methods=["GET", "POST", "HEAD"])
async def create_user() -> dict:
    """No-op — single-user serve."""
    return envelope()


@router.api_route("/updateUser", methods=["GET", "POST", "HEAD"])
@router.api_route("/updateUser.view", methods=["GET", "POST", "HEAD"])
async def update_user() -> dict:
    """No-op — single-user serve."""
    return envelope()


@router.api_route("/deleteUser", methods=["GET", "POST", "HEAD"])
@router.api_route("/deleteUser.view", methods=["GET", "POST", "HEAD"])
async def delete_user() -> dict:
    """No-op — single-user serve."""
    return envelope()


# ---------------------------------------------------------------------------
# Avatar — clients hit this to display a user pic next to nowPlaying entries
# ---------------------------------------------------------------------------


@router.api_route("/getAvatar", methods=["GET", "POST", "HEAD"])
@router.api_route("/getAvatar.view", methods=["GET", "POST", "HEAD"])
async def get_avatar(username: str = Query(default="")) -> dict:
    """Empty avatar — clients show a default placeholder when the body is empty."""
    del username
    return envelope()
