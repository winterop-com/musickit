# `musickit library`

Audit the converted library, tree-render it, fix the deterministic problems, and manage the persistent index DB.

```bash
musickit library DIR [--audit | --issues-only | --fix [--prefer-dirname] [--dry-run]]
musickit library DIR --index-status | --drop-index | --full-rescan | --no-cache
```

`DIR` is required.

## Modes

```bash
musickit library ./output                       # rich.Tree of artists / albums / tracks
musickit library ./output --audit               # tree + per-album warnings column
musickit library ./output --issues-only         # only flagged albums
musickit library ./output --fix                 # apply deterministic fixes
musickit library ./output --fix --dry-run       # preview the fixes
musickit library ./output --fix --prefer-dirname  # invert tag/dir resolution
musickit library ./output --index-status        # show index DB metadata + counts
musickit library ./output --full-rescan         # rebuild the index DB from scratch
musickit library ./output --drop-index          # delete <DIR>/.musickit/ and exit
musickit library ./output --no-cache            # skip the index DB (in-memory only)
```

## Audit rules

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

## `--fix`

Applies the deterministic fixes:

1. **Missing year** — query MusicBrainz for `(album, artist)`, accept the top match's date if score ≥ 90. Writes the year tag back to every track via `apply_tag_overrides`. Reflects in the in-memory model so the next step can see it.
2. **Tag/path mismatch** — by default, **the tag wins**: the directory gets renamed to `naming.album_folder(tag_album, tag_year)`. Pass `--prefer-dirname` to invert: rewrite tags from the dir name (use this when you've hand-curated the dir layout and want tags to follow).
3. The two fixes chain: missing-year fixes first so the rename below sees the new year.

`--dry-run` prints what would change without writing anything.

Fixes that are NOT auto-applied:
- Adding a missing cover — use `musickit cover-pick` for the semi-automated flow.
- Splitting an over-merged album — manual.
- Re-tagging mixed-album_artist albums to a single value — `musickit retag` per dir.

## Output formats

Default tree-render uses `rich.Tree`. Albums show `<year> · <album> (<track-count>)` with a `⚠` badge if `audit` flagged warnings.

```
Various Artists
├── 1998 - Best Of Dance Hits of the 90-98s (18) ⚠
└── 1998 - Party Hits (18) ⚠

Imagine Dragons
└── 2012 - Night Visions (11)
```

Under `--audit` or `--issues-only`, an extra "Warnings" column shows the rule output.

## Companion: `cover-pick`

For the missing/low-res-cover warnings, the manual workflow:

```bash
musickit cover-pick ./output                          # all flagged albums
musickit cover-pick ./output --all                    # every album, even ones with covers
musickit cover-pick ./output --no-browser             # print URL instead of opening it
```

Per-album loop:

1. Print `Artist — Album (no cover)` line.
2. Open `https://covers.musichoarders.xyz/?artist=...&album=...` in your browser.
3. Click any cover on the page (musichoarders' UI copies the URL).
4. Paste back into the terminal — `s` to skip, `q` to quit.
5. We download, validate via Pillow, resize to fit `--cover-max-edge`, save as `cover.jpg`, and (with `--embed`, default) re-embed into every track.

Honours [musichoarders' integration policy](https://covers.musichoarders.xyz/) — never scrapes the site, just pre-fills the search and lets you pick.

## Persistent index DB

The first scan of any library writes a SQLite cache at `<DIR>/.musickit/index.db`. On every subsequent launch — `library`, `tui`, or `serve` — the in-memory `LibraryIndex` is hydrated from rows instead of re-reading every audio file's tags. A delta-validate pass then reconciles the DB against any filesystem changes that happened since the last run (added albums, removed albums, tag edits applied with another tool).

The DB is fully derived from the filesystem, so it's always safe to delete: `rm -rf <DIR>/.musickit/` (or `musickit library DIR --drop-index`). It'll be rebuilt on the next scan.

### Schema

| Table | Holds |
|---|---|
| `meta` | `schema_version`, `library_root_abs`, `last_full_scan_at` |
| `albums` | One row per album dir — tags, counts, `dir_mtime`, audit-relevant flags |
| `tracks` | One row per audio file — tags, ReplayGain, `file_mtime`, `file_size` |
| `track_genres` | `(track_id, genre)` pairs for multi-genre support |
| `album_warnings` | `(album_id, warning)` pairs from the audit pass |

Schema changes don't run migrations — `db.py` defines a `SCHEMA_VERSION` constant; if the on-disk version doesn't match, the DB is unlinked and rebuilt from scratch.

### Management commands

```bash
musickit library DIR --index-status   # schema version, library_root_abs, row counts, DB size
musickit library DIR --drop-index     # delete <DIR>/.musickit/ — next scan recreates it
musickit library DIR --full-rescan    # wipe + rebuild every row, ignoring the cache
musickit library DIR --no-cache       # skip the DB entirely; in-memory scan only
```

`--no-cache` is the right call for read-only mounts where `<DIR>/.musickit/` can't be created. Otherwise, the cache is the default everywhere.

### Cold-start flow

1. `open_db(root)` opens (or creates) `<DIR>/.musickit/index.db`. Mismatched schema or relocated `library_root_abs` triggers an unlink + rebuild.
2. If the DB has no `albums` rows → `scan_full(root, conn)` runs a fresh filesystem walk + audit and writes everything.
3. Otherwise → `load(root, conn)` hydrates the Pydantic graph, then `validate(root, conn)` walks the filesystem, compares per-album `dir_mtime` and per-file `(file_mtime, file_size)` to detect deltas, and re-scans only the affected album dirs via `rescan_albums`.

For the `serve` watcher, `--full-rescan` is what the Subsonic `startScan` endpoint triggers (per-file incremental updates land in a follow-up).
