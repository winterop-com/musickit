"""Stable Subsonic IDs derived from paths — sha1[:16] with a 2-char prefix.

The Subsonic API uses opaque string IDs. We hash each entity's identity
(artist directory name, album path, track path) so IDs survive rescans
and never collide with other types. Reverse lookup is O(1) via the
`IndexCache` dicts in `index.py`.
"""

from __future__ import annotations

import hashlib

from musickit.library.models import LibraryAlbum, LibraryTrack


def artist_id(artist_dir: str) -> str:
    """`ar_<sha1[:16] of artist_dir>`."""
    return "ar_" + hashlib.sha1(artist_dir.encode("utf-8")).hexdigest()[:16]


def album_id(album: LibraryAlbum) -> str:
    """`al_<sha1[:16] of absolute album path>`."""
    return "al_" + hashlib.sha1(str(album.path).encode("utf-8")).hexdigest()[:16]


def track_id(track: LibraryTrack) -> str:
    """`tr_<sha1[:16] of absolute track path>`."""
    return "tr_" + hashlib.sha1(str(track.path).encode("utf-8")).hexdigest()[:16]
