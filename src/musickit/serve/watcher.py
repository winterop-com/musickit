"""Filesystem watcher — auto-trigger a rescan when the library changes.

Drop a new album into the library root, the cache rescans within a few
seconds and clients see the new tracks without needing `/rest/startScan`
or a `serve` restart. Powered by `watchdog`.

Debounced: any FS event resets a timer, the rescan runs once when no
events have arrived for `debounce_s`. A bulk copy of 100 files only
triggers one rescan, not 100. The default 5s window is generous enough
to cover a slow USB / network copy without firing mid-transfer.
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
    """Watch the library root and trigger debounced rescans on FS changes."""

    def __init__(self, cache: IndexCache, *, debounce_s: float = DEFAULT_DEBOUNCE_S) -> None:
        self._cache = cache
        self._debounce_s = debounce_s
        self._observer: Any = None  # watchdog's Observer is a factory; runtime type
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

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
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    def _on_event(self, path: Path) -> None:
        """Called per relevant FS event. Resets the debounce timer."""
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._rescan)
            self._timer.daemon = True
            self._timer.start()
        log.debug("library watcher: change detected at %s; debouncing", path)

    def _rescan(self) -> None:
        log.info("library watcher: triggering background rescan")
        self._cache.start_background_rescan()


class _Handler(FileSystemEventHandler):
    """Forward only audio-file-relevant events to the debounce timer."""

    def __init__(self, on_event_cb: Callable[[Path], None]) -> None:
        super().__init__()
        self._cb = on_event_cb

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Directories: only act on create/delete/move. A "modified" event on a
        # dir fires whenever ANY file inside changes (including .DS_Store
        # writes), which would defeat the audio-extension filter below.
        if event.is_directory:
            if event.event_type not in ("created", "deleted", "moved"):
                return
            self._cb(Path(str(event.src_path)))
            return
        # Files: only audio extensions matter — skip cover.jpg, .DS_Store,
        # backup blobs, etc.
        candidates: list[Path] = []
        if event.src_path:
            candidates.append(Path(str(event.src_path)))
        dest_path = getattr(event, "dest_path", None)
        if dest_path:
            candidates.append(Path(str(dest_path)))
        for path in candidates:
            if path.suffix.lower() in SUPPORTED_AUDIO_EXTS:
                self._cb(path)
                return
