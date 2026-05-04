"""Convert messy audio rips into a clean, tagged, organised library.

Default `audio convert` produces a uniform `.m4a` library at 256 kbps AAC
(`--format auto`); `--format alac` keeps every track lossless. Cover art,
multi-disc layouts, VA / compilation handling, atomic per-album writes,
parallel encoding, optional MusicBrainz / Cover Art Archive enrichment.
"""

from __future__ import annotations

# Single source of truth: read the version from package metadata
# (pyproject.toml `[project] version`). Avoids drift between the lockfile,
# the dist info, and a hardcoded constant in code.
try:
    from importlib.metadata import version as _get_version

    __version__ = _get_version("musickit")
except Exception:  # pragma: no cover — uninstalled / dev tree
    __version__ = "unknown"
