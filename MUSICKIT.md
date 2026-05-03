# musickit — session context dump

This document captures the full design and operational context of `musickit` so a fresh Claude session can pick up where we left off without re-deriving every decision. Read this first if you're resuming work.

## What musickit is

A `uv`-managed Python 3.13 CLI that converts arbitrary audio rips (FLAC / MP3 / M4A / WAV / OGG / OPUS) into a clean, tagged, organised library laid out as `output/<Artist>/<YYYY> - <Album>/NN - <Title>.<ext>`. Built up over many sessions against the user's real ~200 GB rip collection — every edge case it handles came from a real input that exposed it.

Repo: `/Users/morteoh/Music/Audio/` on the user's Mac. Three commits in `main`:

```
4e2f393 Add --overwrite and --remove-source flags for merge-into-existing-output workflow
1671c73 Document edge-case behaviours that surface on real rips
507ff05 Tagless-VA support, classical-layout fix, AcoustID, dry-run reservation
fdc527d Reserve output dir before encode + sync package description
6a0b609 Initial commit: musickit convert pipeline
```

(Plus possibly the verbose-as-global-flag change uncommitted at session pause.)

The user is **morten@winterop.com**, on a Mac. Storage: local `./input` (working space), large library on external `/Volumes/T9/Output/`.

## Audience / user profile

- Listens on AirPods Pro + Sony WH-1000XM6 over Bluetooth. Both decode AAC at 256k over BT — so storing lossless ALAC for daily playback is wasted bytes. **256k AAC m4a is the right default** for this user, and that's what the pipeline does.
- Has a Tailscale network and is interested in building a custom mobile client one day. That's why the future `musickit serve` command targets a Subsonic-compatible REST API.
- Not an audiophile by their own description but technical and prefers concise/honest answers, including pushback. Auto mode is generally on; the user explicitly switches off to discuss.

## Architecture (source tree)

```
src/musickit/
  __init__.py     __main__.py      cli.py          (typer entry)
  pipeline.py     discover.py      metadata.py     (core convert pipeline)
  naming.py       convert.py       cover.py        (filesystem-safe names, ffmpeg, cover sourcing)
  enrich/         _http.py         musicbrainz.py
                  coverart.py      musichoarders.py
                  acoustid.py      __init__.py
tests/            63 unit tests; conftest.py + 6 test_*.py files
pyproject.toml    Makefile         README.md
input/.gitkeep    output/.gitkeep
```

Code style mirrors `~/dev/chap-sdk/chapkit`: ruff (E/W/F/I/D, google docstrings, py313, line-length 120), mypy strict-ish, pyright strict with the same `report*` softeners, pytest + coverage. **All data classes are pydantic** per user's request. Tests + lint run via `make test` / `make lint`.

## Pipeline at a glance

`pipeline.run(input_root, output_root, ...)` orchestrates per-album, sequentially across albums, parallel within (track encoding via `ThreadPoolExecutor`):

1. **Discover** — `discover.discover_albums(root)` walks the input tree, groups by leaf-with-audio-files, then `_merge_disc_siblings` collapses multi-disc layouts. Returns `list[AlbumDir]`.
2. **Read source tags** — `metadata.read_source(path)` per track, dispatched by extension to `_read_flac` / `_read_mp3` / `_read_mp4` / `_read_generic`. Returns `SourceTrack` (pydantic).
3. **Pre-fill from filename** — for tracks lacking title/artist, `_parse_filename_for_va(path)` extracts `Artist - Title` from `NN. Artist - Title.ext`-style filenames. Critical for tagless rips.
4. **AcoustID lookup** (if `--acoustid-key`) — fingerprint via `fpcalc -json`, look up against `https://acoustid.org/v2/lookup`, fill `track.title` + `track.artist` from the highest-confidence recording match (default threshold 0.85).
5. **Summarise** — `summarize_album(tracks)` majority-votes album/album_artist/year/genre, biased toward disc 1 for multi-disc. Detects compilation flag.
6. **Folder fallback** — when ALBUM tag is missing, `naming.clean_folder_album_name(dir.name)` strips codec/quality scene tags + extracts year.
7. **Resolve output paths** — `naming.artist_folder` + `naming.album_folder` + `naming.track_filename`. VA → `Various Artists`. Multi-disc → `01-NN - Title.m4a`.
8. **Cover** — `cover.collect_candidates` gathers embedded pictures + folder.jpg/cover.jpg/etc. siblings, plus online from MusicBrainz/Cover Art Archive when `--enrich`. `cover.pick_best` picks highest pixel area. `cover.normalize` resizes to ≤1000 px JPEG quality 90.
9. **Collision check** — refuse to overwrite an existing output album dir unless `--overwrite`. Reserve `out_dir` in `written_dirs` BEFORE encode (so collisions surface in dry-run too).
10. **Encode** — staging dir `<out_dir_parent>/.<out_dir_name>.staging/`, parallel ThreadPoolExecutor. Per-track `convert.encode` (or `remux_to_m4a` / `copy_passthrough` when `auto_resolve` says copy). Per-track `metadata.write_tags` (dispatches to `write_mp4_tags` / `write_id3_tags` by extension).
11. **Atomic swap** — rename old out_dir to `.<name>.backup`, rename staging into place, drop backup. On any failure, restore backup.
12. **Source removal** (if `--remove-source`) — `shutil.rmtree(_input_footprint(album_dir))` per album, on success only. Footprint computation handles wrapped multi-disc layouts. Refuses to remove the input root itself.
13. **Report** — `AlbumReport` per album (input/output bytes, cover info, warnings, error). `_print_summary` emits a rich table at the end.

## Output format defaults

`--format auto` (the default) targets a uniform `.m4a` library. Per-source dispatch:

| Source codec       | Action                                    |
|---|---|
| FLAC, WAV (lossless) | encode → 256k AAC m4a |
| AAC m4a              | stream-copy (no transcode) |
| ALAC m4a             | encode → 256k AAC m4a |
| MP3 / OGG / Opus     | encode → 256k AAC m4a (one-time tandem encode) |

**Why MP3 transcodes** (rather than remuxes into m4a): Finder / Music.app don't display tags reliably for MP3-in-MP4 hybrids. The convert sacrifices a tiny amount of fidelity (transparent on Bluetooth) for tag visibility everywhere.

Other formats: `--format alac` keeps everything bit-perfect lossless ALAC m4a, `--format aac --bitrate 320k` for higher AAC quality, `--format mp3` for legacy compat.

## Enrichment (online lookups)

- **MusicBrainz release search** at `https://musicbrainz.org/ws/2/release/?query=…&fmt=json`. Accepts top hit only when MB's score ≥ 90 (avoids false positives on common compilation titles). Currently **only the release MBID** is filled into output tags — artist/release-group/track MBIDs are roadmap follow-ups.
- **Cover Art Archive** at `https://coverartarchive.org/release/<MBID>/front-1200`. Fetched after MB resolves an MBID. Treated as another candidate in cover.pick_best (never downgrades local cover).
- **AcoustID** (`--acoustid-key` or `MUSICKIT_ACOUSTID_KEY` env var) — Chromaprint fingerprint via `fpcalc -json`, lookup against AcoustID, fills title+artist on tagless tracks. Off by default (requires user-supplied key + `brew install chromaprint`).
- **musichoarders.xyz** — intentionally **not** scraped under `--enrich`. Their integration policy forbids automated artwork retrieval. A future `musickit cover-pick` subcommand will open their page pre-filled (`?artist=…&album=…`) for manual user pick. URL builder lives at `musickit.enrich.musichoarders.build_search_url`.

`--enrich` is on by default when reachable: `pipeline.run` calls `is_online()` (TCP probe to `musicbrainz.org:443`, 0.5s timeout) once at startup. The `enrich` parameter is tri-state: `None` = auto-probe, `True` = force on (skips probe — useful on networks blocking the probe but allowing HTTP), `False` = off.

## Edge cases the pipeline handles (all backed by tests)

- **Multi-disc folder layouts**: `CD1`/`CD2`, `CD-1`/`CD-2`, `CD 1`/`CD 2`, `Disc 1`/`Disc 2`, `Disk3`, `Album (CD1)`/`Album (CD2)` shared-prefix style, `CD2 (Bonus Live CD)` with trailing text. Merged into one album with proper `disc N/total` tags + `01-NN - …` filenames.
- **Mixed-content parents** (Ultimate Queen layout): only matching-prefix disc pairs merge; bare albums stay as-is.
- **Parent-with-audio + disc-subfolder** (SOAD self-titled with duplicate `Disc 1`/`Disc 2` subfolders inside): drop the disc subfolders, use parent's top-level tracks as one album.
- **Filename-encoded multi-disc** (Zara Larsson `1-01.`/`2-01.` style): `_maybe_apply_filename_disc_track` detects + applies disc/track from the filename pattern when no folder-disc structure exists.
- **VA album with `album_artist = "VA"`/`Various`/`Various Artists`/`V.A.`/`V/A`**: routes to `Various Artists/`, sets `cpil` (MP4) / `TCMP` (ID3) compilation flag.
- **Tagless VA with per-track artist as `VA` placeholder + filename `01 - VA - Artist - Title.flac`**: filename parser splits out the real per-track artist, summary detects compilation by VA-marker artist_fallback.
- **Tagless 100-track VA mix** (7Os8Os9Os style, no tags at all): filename pre-fill in `_process_album` populates per-track artists before summarise → distinct artists triggers compilation flag → output lands under `Various Artists/`.
- **Compilation filename format**: `01-NN - Artist - Title.m4a` (artist included only for compilations; same-artist albums get `01 - Title.m4a`).
- **Disc-suffix in album tags** (`Roses (CD1)`, `Are You Ready: Best Of AC/DC [CD1]`, `Echoes ... (1)` bare-paren disc index): `clean_album_title` strips them. Even handles middle-of-string markers like `Roses (CD2) Live In Madrid` and bare trailing `(1)`/`(2)` disc indices.
- **Disc-1 bias in album-name vote**: bonus-disc tags like `Album (CD2) Live In Madrid` would otherwise win majority-count over disc-1's plain `Album` tag. Fixed by biasing toward disc 1.
- **Folder name junk**: `[FLAC]`, `[16Bit-44.1kHz]`, `(2012)` (year extracted), `VA -` prefix, `[nextorrent.com]`/`[example.org]` scene tags (limited to known TLDs to avoid stripping `[Live]` / `[PMEDIA]` / catalog numbers).
- **Filesystem-safe sanitisation**: `/`/`:`/`*`/`?`/`"`/`<`/`>`/`|` all replaced. `R.E.M.` trailing dots preserved. NFC unicode normalisation. ≤180-byte component cap.
- **Smart quotes / em-dashes** (Sting `1984–1994`): preserved verbatim.
- **Filename collisions** (two tracks with same track-no + title in one album): auto-suffix `(2)`, `(3)`. Reserves the FINAL filename, not just the planned one, so a third track titled `Same (2)` chains to `(3)` rather than colliding with the auto-renamed second.
- **Output dir collisions** (two source albums normalising to the same output path): skip second + warn. Surfaces in dry-run too. Reservation happens BEFORE encode so dry-run and real-run agree.
- **Atomic per-album writes**: hidden `.staging` sibling, swap on success, no half-replaced albums on mid-encode failure.
- **Per-track failures**: caught at thread level, propagate to album-level `error` (album fails in summary, CLI exits non-zero, prior album content preserved by atomic swap).
- **Cover corruption**: `_measure` returns `(0, 0)` on Pillow failure → candidate dropped. `normalize()` exception → fall through to next-best candidate.
- **CAA HTTP 5xx** (Internet Archive CDN flaky): caught + warning + fall back to local cover. Don't crash the album.
- **Lossy → lossy guard**: `--format aac/mp3` against an MP3/OGG source falls back to ALAC for that track unless `--allow-lossy-recompress`. AUTO never triggers this since MP3 explicitly transcodes to AAC by design.
- **MP3-in-MP4 deliberately avoided**: AUTO transcodes MP3 → AAC rather than remuxing — Finder doesn't display tags reliably for the hybrid.
- **Output extension always lowercased**.
- **Cross-parent same-name disc dirs** (`Album A/CD1` + `Album B/CD1`): not merged across parents — each is its own single-disc album.
- **Singleton disc dirs** (`Album/CD1` only, no CD2): not promoted to multi-disc; treated as a regular album.

## Edge cases the pipeline does NOT handle (deliberate choices)

- **"Radio Show -" pseudo-artists** (Armin's ASOT episodes tagged `album_artist = "Radio Show - A State Of Trance"`): we trust whatever tag the rip used. Episodes land in their own pseudo-artist folder. Defended as the right default — keeps weekly shows out of the main artist library. Workarounds: re-tag source files, or future `--artist-override` flag.
- **Classical "Best Of / Beethoven, Mozart, …" wrappers**: NOT coalesced into a single Various Artists comp. We tried and concluded per-composer artist folders were the better default. Workaround: re-tag source with a shared ALBUM tag.

Both documented in README's "Edge-case behaviours worth knowing" section.

## CLI shape

```
musickit [-v|--verbose] convert [INPUT_DIR] [OUTPUT_DIR] [...flags]
musickit inspect PATH
```

`-v`/`--verbose` is a top-level callback flag — works in any position (`musickit -v convert …` or `musickit convert … --verbose`).

Convert flags (most useful first):

- `INPUT_DIR` (default `./input`)
- `OUTPUT_DIR` (default `./output`)
- `--format / -f auto|alac|mp3|aac` (default `auto` = uniform 256k AAC m4a)
- `--bitrate / -b 192k|256k|320k` (default `256k`, ignored for ALAC)
- `--enrich/--no-enrich` (default: auto-probe, on if reachable)
- `--dry-run` (plan only, no writes)
- `--overwrite/--no-overwrite` (default off — existing albums preserved, run merges into existing output)
- `--remove-source/--no-remove-source` (default off — when on, deletes each input album dir on successful convert)
- `--allow-lossy-recompress` (off; opt into MP3→AAC tandem encode under `--format aac`)
- `--workers / -w N` (default `cpu_count // 2`, 0 = auto)
- `--cover-max-edge PX` (default 1000)
- `--acoustid-key TEXT` (or `MUSICKIT_ACOUSTID_KEY` env var; off by default)

## Roadmap (in README, ordered by smallest-payoff-first)

1. **Push to GitHub** — `gh repo create musickit --public --source . --push`. Not yet done.
2. **`musickit library` command + scanner** (task #20 in-progress) — walks the converted output dir, builds `Artist → Album → Track` index from MP4 atoms, prints as `rich.Tree`. Foundation for both TUI and serve. Half a day, no new deps.
3. **`musickit tui`** — Textual app, three-pane (artist/album/track) navigation, `mpv` subprocess for playback, now-playing footer. 1–2 days. Adds `textual` dep.
4. **`musickit serve`** — Subsonic-compatible REST API. SQLite-backed catalog (mtime-incremental), FastAPI routes for `getArtists`/`getArtist`/`getAlbum`/`stream`/`getCoverArt`/`search3`/`ping`. Tailscale-friendly. Adds `fastapi`+`uvicorn` deps. 3–5 days. Unlocks Symfonium / DSub / Substreamer + custom mobile client over Tailscale.
5. **Smaller follow-ups**:
   - `musickit cover-pick` (musichoarders manual flow, ~1 hour)
   - Fill in artist/release-group/track MBIDs from existing MB query (~3 hours)
   - Folder-name cleanup of arbitrary live-venue parens (~1 hour)
   - chromaprint/AcoustID auto-enable for tagless tracks if user has stored key

## Operational state at session pause

- 63 tests passing, lint + mypy + pyright clean.
- Three commits on `main`, working tree may have an uncommitted change for the `-v`-as-global-callback work.
- User has run a 24 GB / 1030-track first batch successfully (output at `/Volumes/T9/Output/` already has 12 artist folders).
- Currently about to run a much larger 195 GB / 7108-track batch to `/Volumes/T9/Output/` with `--remove-source`.

## Resume-from-fresh-session checklist

1. `cd /Users/morteoh/Music/Audio` and `git status` — confirm clean tree or commit pending changes.
2. `make lint && make test` — baseline (should be 63+ tests, all pass, no lint errors).
3. `git log --oneline | head` — confirm the commits listed above.
4. Read `README.md` for user-facing docs and the edge-cases section.
5. Look at the current task list (TaskList) — pending items #19 (serve) and #20 (library scanner).
6. Ask the user what they want to work on next. Default suggestion: `musickit library` command (task #20, foundation for TUI + serve).
