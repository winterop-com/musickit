"""Greedy playlist builder anchored to a seed track.

The walk:
  1. Score every candidate track in the library against the seed.
  2. Sort high → low; iterate.
  3. Pick a track if it doesn't violate the per-album / per-artist caps
     and the playlist isn't full yet.
  4. Stop when accumulated duration >= target, or pool is exhausted.

We score against the seed (not the last-picked track) so the playlist
stays anchored to a coherent feel instead of drifting into whatever
genre the chain happens to wander toward.

Output ordering: the seed first, then by score descending. Some users
might prefer "shuffle within similarity bucket" — easy to add later as
an `order=` knob.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from musickit.library.models import LibraryIndex, LibraryTrack
from musickit.playlist.similarity import score


@dataclass(frozen=True)
class PlaylistResult:
    """Result of `generate()` — tracks plus the resolved name."""

    tracks: list[LibraryTrack]
    name: str
    target_seconds: float
    actual_seconds: float


# Per-album cap: avoid "shuffle dumps a whole album in" effect.
_MAX_PER_ALBUM: int = 2

# Per-artist cap: keeps a 60-min mix from being 12 tracks by one artist.
_MAX_PER_ARTIST: int = 4


def _track_artist_key(track: LibraryTrack) -> str:
    """Lowercased album_artist (fallback artist) for cap accounting."""
    raw = track.album_artist or track.artist or ""
    return raw.strip().lower()


def _track_album_key(track: LibraryTrack) -> str:
    """`(artist, album)` lowercased — caps are per-album, not per-album-title."""
    artist = (track.album_artist or track.artist or "").strip().lower()
    album = (track.album or "").strip().lower()
    return f"{artist}\x00{album}"


def _resolve_seed(index: LibraryIndex, seed: LibraryTrack | str) -> LibraryTrack:
    """Accept a `LibraryTrack` or a string path matching `track.path`."""
    if isinstance(seed, LibraryTrack):
        return seed
    needle = str(seed)
    for album in index.albums:
        for track in album.tracks:
            if str(track.path) == needle:
                return track
            if track.path.name == needle:
                return track
    raise ValueError(f"seed not found in library index: {seed!r}")


def generate(
    index: LibraryIndex,
    seed: LibraryTrack | str,
    *,
    target_minutes: float = 60.0,
    name: str | None = None,
    random_seed: int | None = None,
) -> PlaylistResult:
    """Generate a playlist anchored to `seed`.

    `seed` may be a `LibraryTrack` directly or a string that matches
    either an absolute path or a bare filename inside the index.
    `target_minutes` is the desired length; the actual length will be
    within roughly one track of the target.
    """
    seed_track = _resolve_seed(index, seed)
    target_seconds = max(1.0, target_minutes) * 60.0

    rng = random.Random(random_seed)

    # Build the candidate pool — every track in the index except the seed.
    pool: list[LibraryTrack] = []
    for album in index.albums:
        for track in album.tracks:
            if track.path == seed_track.path:
                continue
            pool.append(track)

    # Score once against the seed; tiny random jitter for tie-breaking.
    scored: list[tuple[float, LibraryTrack]] = []
    for track in pool:
        s = score(seed_track, track) + rng.uniform(0.0, 0.001)
        scored.append((s, track))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    selected: list[LibraryTrack] = [seed_track]
    accumulated = float(seed_track.duration_s or 0.0)
    artist_counts: dict[str, int] = defaultdict(int)
    album_counts: dict[str, int] = defaultdict(int)
    artist_counts[_track_artist_key(seed_track)] += 1
    album_counts[_track_album_key(seed_track)] += 1

    for _, track in scored:
        if accumulated >= target_seconds:
            break
        artist_key = _track_artist_key(track)
        album_key = _track_album_key(track)
        if artist_counts[artist_key] >= _MAX_PER_ARTIST:
            continue
        if album_counts[album_key] >= _MAX_PER_ALBUM:
            continue
        selected.append(track)
        accumulated += float(track.duration_s or 0.0)
        artist_counts[artist_key] += 1
        album_counts[album_key] += 1

    resolved_name = name or _default_name(seed_track)
    return PlaylistResult(
        tracks=selected,
        name=resolved_name,
        target_seconds=target_seconds,
        actual_seconds=accumulated,
    )


def _default_name(seed: LibraryTrack) -> str:
    """Sensible default name when the user didn't pass `--name`."""
    title = seed.title or seed.path.stem
    artist = seed.album_artist or seed.artist or "Unknown"
    return f"Mix - {artist} - {title}"
