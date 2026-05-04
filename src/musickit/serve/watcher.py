"""Filesystem watcher — auto-trigger an incremental rescan when the library changes.

Drop a new album into the library root and the cache absorbs it within
a few seconds without needing `/rest/startScan` or a `serve` restart.
Powered by `watchdog`.

Two-stage flow:

1. Each relevant FS event is recorded in `_pending_paths` under a lock.
2. A single debounce timer fires when no new event has arrived for
   `debounce_s`; on fire it hands the *whole batch* of paths to
   `IndexCache.rescan_paths(paths)`. A bulk copy of 100 files therefore
   triggers one re-scan that touches only the affected albums — not 100
   re-scans, and not a full library rebuild.

The default 5s window is generous enough to cover a slow USB / network
copy without firing mid-transfer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from musickit.metadata import SUPPORTED_AUDIO_EXTS
from musickit.serve.index import IndexCache

log = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_S = 5.0


class LibraryWatcher:
    """Watch the library root and trigger debounced incremental rescans on FS changes."""

    def __init__(self, cache: IndexCache, *, debounce_s: float = DEFAULT_DEBOUNCE_S) -> None:
        self._cache = cache
        self._debounce_s = debounce_s
        self._observer: Any = None  # watchdog's Observer is a factory; runtime type
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()
        # Paths accumulated since the last fire — re-scanned per-album when
        # the debounce window expires. A `set` collapses dup events from
        # watchdog's coarse FS notifications (one save → multiple events).
        self._pending_paths: set[Path] = set()

    def start(self) -> None:
        """Begin watching `cache.root`. No-op if already running."""
        if self._observer is not None:
            return
        if not self._cache.root.exists():
            log.warning("library watcher: %s does not exist; not starting", self._cache.root)
            return
        handler = _Handler(self._on_event)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._cache.root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        """Stop the observer + cancel any pending debounce timer."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending_paths.clear()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    def _on_event(self, path: Path) -> None:
        """Called per relevant FS event. Records the path + resets the debounce timer."""
        with self._timer_lock:
            self._pending_paths.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._rescan)
            self._timer.daemon = True
            self._timer.start()
        log.debug("library watcher: change detected at %s; debouncing", path)

    def _rescan(self) -> None:
        """Drain `_pending_paths` and dispatch an incremental rescan."""
        with self._timer_lock:
            paths = self._pending_paths
            self._pending_paths = set()
            self._timer = None
        if not paths:
            return
        log.info("library watcher: incremental rescan over %d path(s)", len(paths))
        self._cache.rescan_paths(paths)


class _Handler(FileSystemEventHandler):
    """Forward only audio-file-relevant events to the debounce timer."""

    def __init__(self, on_event_cb: Callable[[Path], None]) -> None:
        super().__init__()
        self._cb = on_event_cb

    def on_any_event(self, event: FileSystemEvent) -> None:
        candidates: list[Path] = []
        if event.src_path:
            candidates.append(Path(str(event.src_path)))
        dest_path = getattr(event, "dest_path", None)
        if dest_path:
            candidates.append(Path(str(dest_path)))

        if event.is_directory:
            # Only act on create/delete/move. A "modified" event on a dir
            # fires whenever ANY file inside changes (including .DS_Store
            # writes), which would defeat the audio-extension filter below.
            if event.event_type not in ("created", "deleted", "moved"):
                return
            # Fire BOTH src and dest for moves so the cache can rescan the
            # old AND new album-dir locations after a rename.
            for path in candidates:
                self._cb(path)
            return
        # Files: only audio extensions matter — skip cover.jpg, .DS_Store,
        # backup blobs, etc. Fire each audio candidate so a file move across
        # album dirs registers both source and destination.
        for path in candidates:
            if path.suffix.lower() in SUPPORTED_AUDIO_EXTS:
                self._cb(path)
