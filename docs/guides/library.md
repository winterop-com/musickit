# `musickit library`

Every operation that reads, mutates, or manages the converted library lives under `musickit library`.

```bash
uvx musickit library tree DIR              # rich.Tree of artists / albums / tracks
uvx musickit library audit DIR             # audit table with per-album warnings
uvx musickit library fix DIR               # apply deterministic fixes
uvx musickit library cover IMAGE DIR       # embed an image into every audio file
uvx musickit library cover-pick DIR        # semi-automated cover sourcing via musichoarders
uvx musickit library retag DIR             # in-place tag overrides
uvx musickit library index status DIR      # show index DB metadata + counts
uvx musickit library index drop DIR        # delete <DIR>/.musickit/
uvx musickit library index rebuild DIR     # rebuild the index DB from scratch
```

`DIR` is required for every subcommand.

## `tree` and `audit`

```bash
uvx musickit library tree   DIR [--json] [--no-cache] [--full-rescan]
uvx musickit library audit  DIR [--issues-only] [--json] [--no-cache] [--full-rescan]
```

`tree` prints the `rich.Tree` view; `audit` prints the warnings table.

```
Various Artists
├── 1998 - Best Of Dance Hits of the 90-98s (18) ⚠
└── 1998 - Party Hits (18) ⚠

Imagine Dragons
└── 2012 - Night Visions (11)
```

`--issues-only` on `audit` filters to flagged albums.

### Audit rules

Each rule appends to `album.warnings`. Multiple can fire on one album.

| Rule | Triggers when |
|---|---|
| `no cover` | No embedded picture in any track AND no `cover.jpg` / `folder.jpg` / `front.jpg` sidecar. |
| `low-res cover (Npx)` | Cover smaller than 500×500 px. |
| `missing year` | No track has a `year` tag (after `_year_only` extraction). |
| `mixed years: [...]` | Tracks disagree on year. |
| `mixed album_artist: [...]` | Tracks disagree on album_artist (and the album isn't a compilation). |
| `scene residue in album dir: '...'` | Album dirname has scene-rip residue (square brackets, FLAC tags, etc.). |
| `scene residue in album tag: '...'` | Same in the ALBUM tag. |
| `scene-domain artist dir: 'somesite.com'` | Artist directory looks like a scene-rip provenance string. |
| `album dir is 'Unknown'` | Self-explanatory. |
| `artist is 'Unknown Artist'` | Self-explanatory. |
| `tag/path mismatch: tag=... dir=...` | The album's ALBUM tag doesn't match the directory name (NFC + casefold compare). |
| `track gaps: missing [N, M, ...]` | Per-disc gaps — disc 1 missing track 4, etc. Smart enough to recognise continuous numbering across discs (mega-comps where disc 2's track 1 is numbered 10) and not flag those. |
| `disc N track gaps: missing [...]` | Per-disc gaps on multi-disc albums. |
| `no tracks read` | Album dir contains files but mutagen couldn't parse any. |

## `fix`

```bash
uvx musickit library fix DIR [--dry-run] [--prefer-dirname] [--no-cache] [--full-rescan]
```

Applies the deterministic fixes:

1. **Missing year** — query MusicBrainz for `(album, artist)`, accept the top match's date if score ≥ 90. Writes the year tag back to every track via `apply_tag_overrides`. Reflects in the in-memory model so the next step can see it.
2. **Tag/path mismatch** — by default, **the tag wins**: the directory gets renamed to `naming.album_folder(tag_album, tag_year)`. Pass `--prefer-dirname` to invert: rewrite tags from the dir name (use this when you've hand-curated the dir layout and want tags to follow).
3. The two fixes chain: missing-year fixes first so the rename below sees the new year.

`--dry-run` prints what would change without writing anything.

Fixes that are NOT auto-applied:
- Adding a missing cover — use `library cover-pick` (semi-automated) or `library cover IMAGE` (you provide the file).
- Splitting an over-merged album — manual.
- Re-tagging mixed-album_artist albums to a single value — `library retag` per dir.

## `cover` — embed an image

```bash
uvx musickit library cover IMAGE DIR [--cover-max-edge PX] [--recursive/--no-recursive]
```

Embeds `IMAGE` (JPG/PNG) into every audio file under `DIR`. The image is normalised once (downscaled to fit the long-edge cap, JPEG-encoded for non-PNG sources) and then written to every supported audio file. Other tags are preserved — only the cover is replaced.

```bash
uvx musickit library cover ./output/Pink\ Floyd/1973\ -\ The\ Dark\ Side\ Of\ The\ Moon scan-of-the-LP.jpg
```

## `cover-pick` — semi-automated cover sourcing

```bash
uvx musickit library cover-pick DIR [--all] [--no-embed] [--cover-max-edge PX] [--no-browser]
```

For each candidate album:

1. Print the album line + audit reason.
2. Open the [musichoarders.xyz](https://covers.musichoarders.xyz/) pre-fill URL in your browser.
3. Click any cover on the site to copy its URL (musichoarders' UI does this).
4. Paste the URL back into the terminal — `s` to skip, `q` to quit.
5. We download, validate, resize, save as `cover.jpg`, and (with `--embed`, default) re-embed into every track.

By default only flagged albums (no cover or low-res) are surfaced. Pass `--all` to walk every album.

Honours [musichoarders' integration policy](https://covers.musichoarders.xyz/) — never scrapes the site, just pre-fills the search and lets you pick.

## `retag` — in-place tag overrides

```bash
uvx musickit library retag DIR [--title T] [--artist A] [--album-artist AA] [--album AL] \
                                   [--year YYYY] [--genre G] \
                                   [--track-total N] [--disc-total N] \
                                   [--recursive/--no-recursive] [--rename]
```

Only fields you explicitly pass are written; everything else is preserved (including covers, replaygain, MusicBrainz IDs). Useful when an album converted with the wrong name and you don't want to re-encode just to fix a tag.

```bash
uvx musickit library retag path/to/album/01.m4a --year 1976
uvx musickit library retag path/to/album --track-total 12
uvx musickit library retag path/to/album --genre ''
```

`--rename` renames `DIR` to `YYYY - Album` based on the post-update tags after the retag completes.

## `lyrics` — fetch synced lyrics from LRCLIB

```bash
uvx musickit library lyrics fetch DIR                  # populate missing sidecars
uvx musickit library lyrics fetch DIR --dry-run        # show intent without hitting the network
uvx musickit library lyrics fetch DIR --all            # re-fetch every track (use sparingly)
```

For each track without lyrics — no embedded `\xa9lyr` / `USLT` / `LYRICS` tag and no existing `<track>.lrc` sidecar — query [LRCLIB](https://lrclib.net) (free, no API key) and, on a hit, write the result as `<track>.flac.lrc` (or whatever the audio suffix is). Synced bodies (`syncedLyrics` field) are preferred over plain when LRCLIB returns both.

Sidecars take precedence over embedded tags on the next library scan, so user-edited `.lrc` files survive rescans untouched. The TUI's `l` keybind and the server's `/getLyricsBySongId` both pick up the populated lyrics automatically — synced bodies render with a live time-tracked highlight.

The command exits non-zero if more than 10% of attempted fetches raise transport errors (HTTP 5xx, timeout, malformed JSON) — early signal of a network outage or LRCLIB API change. 404s ("no match in LRCLIB for this track") do not count toward the failure rate; common for live recordings, deep cuts, and non-English tracks.

## `index` — manage the persistent SQLite cache

The first scan of any library writes a SQLite cache at `<DIR>/.musickit/index.db`. On every subsequent launch — `library`, `tui`, or `serve` — the in-memory `LibraryIndex` is hydrated from rows instead of re-reading every audio file's tags. A delta-validate pass then reconciles the DB against any filesystem changes that happened since the last run (added albums, removed albums, tag edits applied with another tool).

The DB is fully derived from the filesystem, so it's always safe to delete.

### Commands

```bash
uvx musickit library index status  DIR     # schema version, library_root_abs, row counts, DB size
uvx musickit library index drop    DIR     # delete <DIR>/.musickit/
uvx musickit library index rebuild DIR     # wipe + rebuild from scratch
```

`--no-cache` (on `tree` / `audit` / `fix` / `tui` / `serve`) skips the DB entirely — useful for read-only mounts where `<DIR>/.musickit/` can't be created. `--full-rescan` (on the same set) rebuilds the index from scratch on this run. `cover-pick` uses the existing in-memory scan and doesn't expose either flag.

### Schema

| Table | Holds |
|---|---|
| `meta` | `schema_version`, `library_root_abs`, `last_full_scan_at` |
| `albums` | One row per album dir — tags, counts, `dir_mtime`, audit-relevant flags |
| `tracks` | One row per audio file — tags, ReplayGain, `file_mtime`, `file_size` |
| `track_genres` | `(track_id, genre)` pairs for multi-genre support |
| `album_warnings` | `(album_id, warning)` pairs from the audit pass |

Schema changes don't run migrations — `db.py` defines a `SCHEMA_VERSION` constant; if the on-disk version doesn't match, the DB is unlinked and rebuilt from scratch.

### Cold-start flow

1. `open_db(root)` opens (or creates) `<DIR>/.musickit/index.db`. Mismatched schema or relocated `library_root_abs` triggers an unlink + rebuild.
2. If the DB has no `albums` rows → `scan_full(root, conn)` runs a fresh filesystem walk + audit and writes everything.
3. Otherwise → `load(root, conn)` hydrates the Pydantic graph, then `validate(root, conn)` walks the filesystem, compares per-album `dir_mtime` and per-file `(file_mtime, file_size)` to detect deltas, and re-scans only the affected album dirs via `rescan_albums`.

For the `serve` watcher, `--full-rescan` is what the Subsonic `startScan` endpoint triggers (per-file incremental updates land in a follow-up).
