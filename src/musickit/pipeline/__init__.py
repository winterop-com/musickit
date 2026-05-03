"""Orchestrator: per-album discover → cover → convert → tag → report.

Public surface re-exported here so callers keep using
`from musickit import pipeline` / `from musickit.pipeline import …`.
The names with leading underscores are still package-private but are
re-exported because tests import them directly.
"""

from __future__ import annotations

from musickit.pipeline.dedupe import _dedupe_duplicate_tracks
from musickit.pipeline.filenames import _humanise_slug
from musickit.pipeline.footprint import _input_footprint
from musickit.pipeline.report import AlbumReport
from musickit.pipeline.run import default_workers, run

__all__ = [
    "AlbumReport",
    "_dedupe_duplicate_tracks",
    "_humanise_slug",
    "_input_footprint",
    "default_workers",
    "run",
]
