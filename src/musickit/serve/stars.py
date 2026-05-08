"""Persistent stars / favourites store at `<root>/.musickit/stars.toml`.

Subsonic clients (Symfonium, Amperfy, Feishin, etc.) expose a heart /
star button that hits `/star`, `/unstar`, `/getStarred`, `/getStarred2`
on the server. Until v0.6.4 those endpoints were no-ops — clients
showed the button but a tap had no effect.

Stars live OUTSIDE the SQLite library index. The index is fully
derived from the filesystem (delete and rebuild = safe); stars are
genuine user data, so they need their own home that survives schema
bumps and `library index drop`. TOML keeps them human-readable and
hand-editable — same shape as `radio.toml` and `serve.toml`.

The store keys on the same Subsonic IDs the cache mints (`ar_xxxx`,
`al_xxxx`, `tr_xxxx`). Because IDs are sha1[:16] of stable identifiers
(artist dir name, album path, track path), a starred entry survives a
library index rebuild — re-scanning the filesystem produces the same
ID for the same file.

If the underlying file is deleted or renamed, the starred entry
becomes a "ghost": still in stars.toml but no longer resolvable
against the cache. `/getStarred*` silently filters those out so the
client never sees broken entries; `Store.prune(cache)` removes them
from the file on demand.
"""

from __future__ import annotations

import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from musickit import _toml_dump

log = logging.getLogger(__name__)


class StarStore:
    """Read / mutate `<root>/.musickit/stars.toml` with file-level locking."""

    def __init__(self, path: Path) -> None:
        """Build a store rooted at `path` (the `stars.toml` file)."""
        self._path = path
        self._lock = RLock()
        # `_items` maps Subsonic ID -> ISO 8601 starred-at timestamp.
        self._items: dict[str, str] = {}
        self._load()

    @classmethod
    def for_root(cls, root: Path) -> StarStore:
        """Return the canonical `<root>/.musickit/stars.toml` store."""
        return cls(root / ".musickit" / "stars.toml")

    @property
    def path(self) -> Path:
        """File location backing this store."""
        return self._path

    def add(self, sid: str) -> None:
        """Star `sid` if not already starred. Idempotent."""
        with self._lock:
            if sid in self._items:
                return
            self._items[sid] = _now_iso()
            self._save()

    def remove(self, sid: str) -> None:
        """Unstar `sid`. Idempotent — silently ignores already-unstarred IDs."""
        with self._lock:
            if sid not in self._items:
                return
            del self._items[sid]
            self._save()

    def is_starred(self, sid: str) -> bool:
        """True iff `sid` is currently starred."""
        with self._lock:
            return sid in self._items

    def starred_at(self, sid: str) -> str | None:
        """ISO 8601 timestamp the ID was starred at, or None if not starred."""
        with self._lock:
            return self._items.get(sid)

    def all_ids(self) -> dict[str, str]:
        """Snapshot copy of `id -> starred_at`."""
        with self._lock:
            return dict(self._items)

    def by_kind(self, prefix: str) -> dict[str, str]:
        """All IDs whose prefix matches (e.g. `"ar_"`, `"al_"`, `"tr_"`)."""
        with self._lock:
            return {sid: ts for sid, ts in self._items.items() if sid.startswith(prefix)}

    def prune(self, valid_ids: set[str]) -> int:
        """Drop starred IDs that are no longer in `valid_ids`. Returns the count removed."""
        with self._lock:
            stale = [sid for sid in self._items if sid not in valid_ids]
            for sid in stale:
                del self._items[sid]
            if stale:
                self._save()
            return len(stale)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read `stars.toml`. Missing file or parse error -> empty store."""
        if not self._path.exists():
            return
        try:
            with self._path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log.warning("stars: failed to read %s (%s); starting empty", self._path, exc)
            return
        items = data.get("items")
        if isinstance(items, dict):
            self._items = {str(k): str(v) for k, v in items.items()}

    def _save(self) -> None:
        """Atomic-write the current state. Best-effort on read-only mounts."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(_toml_dump.dumps({"items": self._items}), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:  # pragma: no cover — read-only mount
            log.warning("stars: failed to write %s (%s); changes lost on restart", self._path, exc)


def _now_iso() -> str:
    """UTC `now()` formatted as Subsonic-compatible ISO 8601 (e.g. 2026-05-05T10:30:00Z)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
