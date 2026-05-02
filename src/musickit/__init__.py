"""Convert messy audio rips into a clean, tagged, organised library.

Default `audio convert` produces a uniform `.m4a` library at 256 kbps AAC
(`--format auto`); `--format alac` keeps every track lossless. Cover art,
multi-disc layouts, VA / compilation handling, atomic per-album writes,
parallel encoding, optional MusicBrainz / Cover Art Archive enrichment.
"""

__version__ = "0.1.0"
