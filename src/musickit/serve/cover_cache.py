"""Byte-bounded LRU for `getCoverArt` responses — keyed `(album_id, size)`.

Mobile clients (Symfonium / Amperfy) request one cover per album row in
the browse list. On a slow disk that's hundreds of sidecar reads + Pillow
decodes per scroll. Caching the encoded response bytes turns the second
through Nth request into a dict lookup.

The cache is **bytes-bounded** rather than count-bounded — a 1500x1500
PNG and a 64x64 JPEG differ ~50x in size, so a count limit either wastes
RAM (sized for big covers) or evicts too aggressively (sized for thumbs).
Default budget is ~64 MiB which fits ~1k thumbnails or ~50 full-size
covers — comfortably more than any real browse session.

Thread-safety: `serve` runs FastAPI under uvicorn with multiple threads,
so reads/writes are guarded by an `RLock`. Eviction is O(1) per entry
via `OrderedDict.popitem(last=False)`.

Invalidation: `IndexCache._reindex` calls `clear()` so a `startScan` or
filesystem-watcher rebuild doesn't leak stale covers from before the
filesystem changed (e.g. a re-cover-picked album).
"""

from __future__ import annotations

import threading
from collections import OrderedDict

_DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class CoverCache:
    """Thread-safe byte-bounded LRU. Keys are `(album_id, size_or_None)`."""

    def __init__(self, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple[str, int | None], tuple[bytes, str]] = OrderedDict()
        self._used_bytes = 0
        self._lock = threading.RLock()
        # Counters surfaced in tests + future telemetry. No public reset —
        # the counters reflect the lifetime of this cache instance, which
        # itself gets recreated on every `_reindex`.
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @property
    def used_bytes(self) -> int:
        """Approximate cached bytes (sum of payload lengths; ignores dict overhead)."""
        return self._used_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: tuple[str, int | None]) -> tuple[bytes, str] | None:
        """Return cached `(bytes, mime)` or None. Promotes the entry to MRU on hit."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, key: tuple[str, int | None], data: bytes, mime: str) -> None:
        """Insert or replace. Evicts LRU entries until under `max_bytes`."""
        size = len(data)
        # Single payloads larger than the entire budget would cause the
        # eviction loop below to immediately discard them. Skip caching
        # those — pathological case (5MB cover + tiny budget); the
        # endpoint just falls through to recompute next time.
        if size > self._max_bytes:
            return
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._used_bytes -= len(existing[0])
            self._entries[key] = (data, mime)
            self._used_bytes += size
            while self._used_bytes > self._max_bytes and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._used_bytes -= len(evicted[0])
                self.evictions += 1

    def clear(self) -> None:
        """Drop every entry — called on `_reindex` so stale covers don't leak across rebuilds."""
        with self._lock:
            self._entries.clear()
            self._used_bytes = 0
