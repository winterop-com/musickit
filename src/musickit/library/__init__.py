"""Walk a converted-output directory, build an Artist→Album→Track index, audit it.

Public surface re-exported here so callers keep using
`from musickit import library` / `from musickit.library import …`.
The leading-underscore helpers `_audit_cover` and `_split_dir_year`
are also re-exported because tests/CLI import them directly.
"""

from __future__ import annotations

from musickit.library.audit import _audit_cover, audit
from musickit.library.fix import fix_album, fix_index
from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.library.scan import _split_dir_year, scan

__all__ = [
    "LibraryAlbum",
    "LibraryIndex",
    "LibraryTrack",
    "_audit_cover",
    "_split_dir_year",
    "audit",
    "fix_album",
    "fix_index",
    "scan",
]
