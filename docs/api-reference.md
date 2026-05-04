# API Reference

Auto-generated from docstrings via [mkdocstrings](https://mkdocstrings.github.io/). The public surface is small — most users only touch the CLI — but if you're embedding musickit in another tool, these are the entry points.

## `musickit.metadata`

Read source audio tags (FLAC / MP3 / generic) and write MP4 ALAC / AAC / MP3 tags.

::: musickit.metadata
    options:
      members:
        - SourceTrack
        - AlbumSummary
        - MusicBrainzIds
        - TagOverrides
        - SUPPORTED_AUDIO_EXTS
        - read_source
        - summarize_album
        - clean_album_title
        - write_tags
        - write_mp4_tags
        - write_id3_tags
        - embed_cover_only
        - apply_tag_overrides

## `musickit.library`

Walk a converted-output directory, build an Artist→Album→Track index, audit it, fix the deterministic warnings.

::: musickit.library
    options:
      members:
        - LibraryTrack
        - LibraryAlbum
        - LibraryIndex
        - scan
        - audit
        - fix_index
        - fix_album

## `musickit.serve`

FastAPI factory + auth + config for the Subsonic-compatible HTTP server.

::: musickit.serve
    options:
      members:
        - create_app
        - resolve_credentials
        - ServeConfig

## `musickit.naming`

Filesystem-safe folder + filename builders.

::: musickit.naming
    options:
      members:
        - artist_folder
        - album_folder
        - track_filename
        - clean_folder_album_name
        - leading_year_from_folder
        - is_various_artists
        - sanitize_component
        - VARIOUS_ARTISTS

## `musickit.cover`

Cover-art candidates, picker, normaliser.

::: musickit.cover
    options:
      members:
        - CoverCandidate
        - CoverSource
        - Cover
        - DEFAULT_MAX_EDGE
        - collect_candidates
        - pick_best
        - normalize
