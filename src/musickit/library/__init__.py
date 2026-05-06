"""Walk a converted-output directory, build an Artist→Album→Track index, audit it.

Public surface re-exported here so callers keep using
`from musickit import library` / `from musickit.library import …`.
The leading-underscore helpers `_audit_cover` and `_split_dir_year`
are also re-exported because tests/CLI import them directly.
"""

from __future__ import annotations

from musickit.library.audit import _audit_cover, audit, audit_album
from musickit.library.db import (
    INDEX_DB_NAME,
    INDEX_DIR_NAME,
    SCHEMA_VERSION,
    db_path,
    is_empty,
    open_db,
)
from musickit.library.fix import fix_album, fix_index
from musickit.library.load import load, load_or_scan
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.library.rename import (
    RenameError,
    RenameResult,
    compute_new_album_path,
    rename_album_to_match_tags,
)
from musickit.library.scan import (
    ScanProgressCallback,
    ValidationResult,
    _split_dir_year,
    rescan_albums,
    scan,
    scan_full,
    validate,
)

__all__ = [
    "INDEX_DB_NAME",
    "INDEX_DIR_NAME",
    "SCHEMA_VERSION",
    "LibraryAlbum",
    "LibraryIndex",
    "LibraryTrack",
    "RenameError",
    "RenameResult",
    "ScanProgressCallback",
    "ValidationResult",
    "_audit_cover",
    "_split_dir_year",
    "audit",
    "audit_album",
    "compute_new_album_path",
    "db_path",
    "fix_album",
    "fix_index",
    "is_empty",
    "load",
    "load_or_scan",
    "open_db",
    "rename_album_to_match_tags",
    "rescan_albums",
    "scan",
    "scan_full",
    "validate",
]
