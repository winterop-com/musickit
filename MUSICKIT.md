# musickit — session context dump

This document captures the full design and operational context of `musickit` so a fresh Claude session can pick up where we left off without re-deriving every decision. Read this first if you're resuming work.

## What musickit is

A `uv`-managed Python 3.13 CLI that converts arbitrary audio rips (FLAC / MP3 / M4A / WAV / OGG / OPUS) into a clean, tagged, organised library laid out as `output/<Artist>/<YYYY> - <Album>/NN - <Title>.<ext>`, then provides a Textual TUI for local playback **and** a Subsonic-compatible HTTP server so any modern Subsonic client (Symfonium, Amperfy, play:Sub, Feishin, Supersonic) can browse / search / stream the library — including remotely over Tailscale.

Built up over many sessions against the user's real ~200 GB rip collection — every edge case it handles came from a real input that exposed it.

Repo: `/Users/morteoh/Music/Audio/` on the user's Mac. The user is **morten@winterop.com**. Storage: local `./input` (working space), large library on external `/Volumes/T9/Output/`.

## Audience / user profile

- Listens on AirPods Pro + Sony WH-1000XM6 over Bluetooth. Both decode AAC at 256k over BT — so storing lossless ALAC for daily playback is wasted bytes. **256k AAC m4a is the right default** for this user, and that's what the convert pipeline does.
- Has a Tailscale network. `musickit serve` is configured to bind `0.0.0.0` by default specifically so the tailnet IP / MagicDNS hostname Just Works without further setup.
- Not an audiophile by their own description but technical and prefers concise/honest answers, including pushback. Auto mode is generally on; the user explicitly switches off to discuss.
- Strong preference: NO emojis anywhere in code, commits, PRs, docs, output. Plain text only (`[x]` not `✓`, `WARNING:` not warning glyph). Codified in `CLAUDE.md`.

## Subcommands

```
musickit [-v|--verbose] convert  [INPUT_DIR] [OUTPUT_DIR] [...flags]    # FLAC/MP3/M4A → clean tagged library
musickit                inspect  PATH                                   # tag + cover summary
musickit                library  [DIR] [--audit | --issues-only | --fix [--prefer-dirname] [--dry-run]]
musickit                retag    PATH [--year YEAR] [--album ALBUM] ... # in-place tag overrides
musickit                cover    PATH COVER_PATH                        # retrofit cover art
musickit                tui      [DIR]                                  # Textual TUI; omit DIR for radio-only
musickit                serve    [DIR] [--host H] [--port P] [--user U] [--password P]
```

`-v`/`--verbose` is a top-level callback flag — works in any position.

## Architecture (source tree, current)

```
src/musickit/
  __init__.py        __main__.py
  cli/               typer entry; one file per subcommand
    __init__.py      app = typer.Typer(...) + side-effect imports
    convert.py       cover.py     inspect.py   library.py
    retag.py         serve.py     tui.py
  convert.py         ffmpeg encode/remux/copy
  cover.py           cover-source candidates + pick_best + normalise
  discover.py        walk input → list[AlbumDir] (with multi-disc merge)
  library/           Artist→Album→Track index of the converted output
    __init__.py      models.py    scan.py      audit.py    fix.py
  metadata/          tag read/write
    __init__.py      models.py    album.py     read.py     write.py    overrides.py
  naming.py          filesystem-safe folder + filename builders
  pipeline/          orchestrator — discover → cover → convert → tag → swap
    __init__.py      run.py       album.py     track.py    report.py   progress.py
    filenames.py     disc.py      dedupe.py    footprint.py acoustid.py
  radio.py           curated radio-station list (NRK defaults + user TOML merge)
  serve/             Subsonic-compatible HTTP server
    __init__.py      app.py       auth.py      config.py
    ids.py           index.py     payloads.py  covers.py
    endpoints/       __init__.py  system.py    browsing.py  media.py   search.py   scan.py
  tui/               Textual TUI + audio player
    __init__.py      app.py       widgets.py   player.py    audio_io.py
    advance.py       commands.py  formatters.py state.py    types.py
  enrich/            __init__.py  _http.py     musicbrainz.py
                     coverart.py  musichoarders.py  acoustid.py
tests/               167 tests; conftest.py + 17 test_*.py files
pyproject.toml       Makefile     README.md   MUSICKIT.md  CLAUDE.md
input/.gitkeep       output/.gitkeep
```

Code style: ruff (E/W/F/I/D, google docstrings, py313, line-length 120), mypy strict-ish, pyright `strict` with `report*` softeners, pytest + coverage. **All data classes are pydantic** per user's request. Tests + lint run via `make test` / `make lint`.

The whole codebase went through a 7-wave refactor (split monolithic modules into packages while keeping public imports stable):

1. `cli.py` (599 lines) → `cli/` package
2. `tui/app.py` (1440 lines) → `tui/` package (widgets/commands/state/app split)
3. `metadata.py` (974 lines) → `metadata/` package
4. `pipeline.py` (1099 lines) → `pipeline/` package (11 submodules)
5. `library.py` (467 lines) → `library/` package
6. `tui/app.py` further trimmed by extracting types/formatters/advance helpers
7. `tui/player.py` extracted `audio_io.py` (PyAV container open + ICY metadata)

## Convert pipeline at a glance

`pipeline.run(input_root, output_root, ...)` orchestrates per-album, sequentially across albums, parallel within (track encoding via `ThreadPoolExecutor`):

1. **Discover** — `discover.discover_albums(root)` walks input, groups by leaf-with-audio-files, then `_merge_disc_siblings` collapses multi-disc layouts.
2. **Read source tags** — `metadata.read_source(path)` per track; dispatches by extension to FLAC / MP3 / MP4 / generic readers. Returns `SourceTrack` (pydantic).
3. **Pre-fill from filename** — for tagless tracks, parse `NN. Artist - Title.ext` style.
4. **AcoustID lookup** (if `--acoustid-key`) — chromaprint fingerprint via `fpcalc -json`, fills title+artist on tagless tracks.
5. **Summarise** — majority-vote album/year/genre, biased toward disc 1.
6. **Folder fallback** — when ALBUM tag is missing, strip codec/quality scene tags + extract year from dir name.
7. **Resolve output paths** — VA → `Various Artists`. Multi-disc → `01-NN - Title.m4a`.
8. **Cover** — embedded > sidecar > MusicBrainz/Cover Art Archive when `--enrich`. Picker keeps highest pixel area, never downgrades.
9. **Collision check** — refuse to overwrite an existing output album dir unless `--overwrite`.
10. **Encode** — staging dir, ThreadPoolExecutor, atomic swap on success.
11. **Source removal** (if `--remove-source`) — `shutil.rmtree(_input_footprint(album_dir))` after the swap succeeds.
12. **Report** — `AlbumReport` per album, `_print_summary` emits a rich table.

## Output format defaults

`--format auto` (the default) targets a uniform `.m4a` library at 256k AAC:

| Source codec       | Action                                    |
|---|---|
| FLAC, WAV (lossless) | encode → 256k AAC m4a |
| AAC m4a              | stream-copy (no transcode) |
| ALAC m4a             | encode → 256k AAC m4a |
| MP3 / OGG / Opus     | encode → 256k AAC m4a (one-time tandem encode) |

**Why MP3 transcodes** rather than remuxing into m4a: Finder / Music.app don't display tags reliably for MP3-in-MP4 hybrids. Tiny fidelity loss (transparent on Bluetooth) for tag visibility everywhere.

Other formats: `--format alac` (bit-perfect lossless), `--format aac --bitrate 320k` (higher AAC quality), `--format mp3` (legacy compat).

## Subsonic server (`musickit serve`)

The recent 3-phase build that brought the API up:

**Phase 1 — auth + envelope** (commit `b006b89`):
- FastAPI app, response-envelope middleware, plain + `enc:` + token auth.
- `~/.config/musickit/serve.toml` config (CLI flags override).
- `--host 0.0.0.0` default for Tailscale; `--port 4533` (Navidrome default).
- Tailscale-aware startup banner: prints LAN IP + MagicDNS hostname.
- Endpoints: `ping`, `getLicense`, `getMusicFolders`.

**Phase 2 — browsing + scan** (commit `2b6432a`):
- `serve/ids.py` — sha1[:16] of artist_dir/album path/track path, prefixed `ar_/al_/tr_`. Stable across rescans.
- `serve/index.py` — `IndexCache` wraps `LibraryIndex` with reverse-lookup dicts; `rebuild()` runs `library.scan` + `library.audit` + populates the maps. Lock-guarded so concurrent rescans collapse.
- CLI runs `cache.rebuild()` synchronously before `uvicorn.run()` so first request hits a populated cache.
- Endpoints: `getArtists`, `getArtist`, `getAlbum`, `getAlbumList2` (alphabeticalByName/Artist, random, byYear, byGenre — others fall back to alphabetical), `getSong`, `getIndexes`, `startScan`, `getScanStatus`.

**Phase 3 — media + search** (this session):
- `serve/payloads.py` — shared album/song/artist payload builders.
- `serve/covers.py` — sidecar (`cover.jpg` / `folder.jpg` / `front.jpg`) first, embedded picture fallback, optional Pillow resize (capped at 1500 px).
- Endpoints: `stream` (FileResponse with `Accept-Ranges: bytes` — Starlette handles HTTP Range natively, returns 206 + `Content-Range`), `download` (alias), `getCoverArt` (with optional `?size=`), `search3` + `search2` (multi-token AND, case-insensitive substring; pagination via `*Count` + `*Offset`).

API version reported: `1.16.1`. Implementation tracks the [OpenSubsonic spec](https://opensubsonic.netlify.app/docs/api-reference/) — the original subsonic.org is dead/paid and shouldn't be the reference any more.

### What clients we tested against (recommended)

The user discovered the original Subsonic ecosystem looked dead (subsonic.org from 2015, abandoned clients). **The actually-active 2026 ecosystem** is documented in the README:

- Android: **Symfonium** (paid, very polished — the one to recommend), Tempo, Ultrasonic, DSub
- iOS: **Amperfy** (FOSS, free), play:Sub, Substreamer
- Desktop: **Feishin**, **Supersonic**

### Tailscale story

Bind to `0.0.0.0` (default), install Tailscale on the server + phone, point client at `<machine-name>.<tailnet>.ts.net:4533`. WireGuard tunnel handles encryption — no HTTPS to set up, no port forwarding. This is exactly why the default isn't `127.0.0.1`.

## TUI (`musickit tui`)

Textual app, three-pane layout:

- **Top**: now-playing meta + 24-band FFT visualizer + progress + state + volume.
- **Sidebar**: stats + Artist→Album browser tree (one screen, drill-in via Enter, back via Backspace).
- **Main**: track list with `▶` marker on the playing row.
- **Bottom**: status bar + keybar.

Decoder in-process via PyAV (bundled FFmpeg, no `brew install`). Output via sounddevice/PortAudio (bundled). Threading: opener thread for HTTP connect (so the previous track keeps playing during stream switches), decoder thread + bounded queue for PCM, sounddevice callback drains chunks across boundaries with carry state. FFT runs on the UI tick (~30 FPS), NOT on the audio callback.

Curated radio-station list in `radio.py` — NRK mP3 / P3 / P3 Musikk / Nyheter baked in, user can add more via `~/.config/musickit/radio.toml`. ICY metadata polling updates "now playing" with the live song name.

## Edge cases the convert pipeline handles (all backed by tests)

- **Multi-disc folder layouts**: `CD1`/`CD2`, `CD-1`/`CD-2`, `CD 1`/`CD 2`, `Disc 1`/`Disc 2`, `Disk3`, `Album (CD1)`/`Album (CD2)` shared-prefix style, `CD2 (Bonus Live CD)` with trailing text. Merged into one album with proper `disc N/total` tags + `01-NN - …` filenames.
- **Mixed-content parents** (Ultimate Queen layout): only matching-prefix disc pairs merge; bare albums stay as-is.
- **Parent-with-audio + disc-subfolder** (SOAD self-titled with duplicate `Disc 1`/`Disc 2` subfolders inside): drop the disc subfolders, use parent's top-level tracks as one album.
- **Filename-encoded multi-disc** (Zara Larsson `1-01.`/`2-01.` style): `_maybe_apply_filename_disc_track` detects + applies disc/track from the filename pattern.
- **Scene-encoded `DTT` track numbers** (`101 = disc 1 track 1`): conservative trigger conditions, decode filename prefix into disc + track.
- **VA album with `album_artist = "VA"`/`Various`/`Various Artists`/`V.A.`/`V/A`**: routes to `Various Artists/`, sets `cpil` (MP4) / `TCMP` (ID3) compilation flag.
- **Tagless VA** (filename `01 - VA - Artist - Title.flac`, or per-track artist as VA placeholder): filename parser splits real per-track artist, summary detects compilation.
- **Tagless 100-track VA mix** (7Os8Os9Os style): filename pre-fill → distinct artists → compilation flag → `Various Artists/`.
- **Disc-suffix in album tags** (`Roses (CD1)`, `Are You Ready: Best Of AC/DC [CD1]`, bare-paren `(1)`/`(2)`): `clean_album_title` strips them. Even handles middle-of-string markers like `Roses (CD2) Live In Madrid`.
- **Disc-1 bias in album-name vote**: bonus-disc tags would otherwise win majority; biased toward disc 1.
- **Folder name junk**: `[FLAC]`, `[16Bit-44.1kHz]`, `(2012)` (year extracted), `VA -` prefix, scene-domain tags (limited to known TLDs).
- **Filesystem-safe sanitisation**: `/`/`:`/`*`/`?`/`"`/`<`/`>`/`|` replaced. `R.E.M.` trailing dots preserved. NFC unicode normalisation. ≤180-byte component cap.
- **Smart quotes / em-dashes** preserved verbatim.
- **Filename collisions** (two tracks with same track-no + title): auto-suffix `(2)`, `(3)`. Reserves the FINAL filename so chained collisions resolve.
- **Output dir collisions** (two source albums normalising to same path): skip second + warn. Surfaces in dry-run too.
- **Source-side dedupe** (rip groups shipping every track twice under different filename conventions): match on `(disc, track, title, artist)` + duration within 0.5s.
- **Atomic per-album writes**: hidden `.staging` sibling, swap on success, no half-replaced albums on mid-encode failure.
- **Per-track failures**: caught at thread level, propagate to album-level `error`.
- **Cover corruption**: candidate dropped on Pillow failure → next-best candidate wins.
- **CAA HTTP 5xx**: caught + warning + fall back to local cover. Don't crash the album.
- **Lossy → lossy guard**: `--format aac/mp3` against MP3/OGG source falls back to ALAC unless `--allow-lossy-recompress`.

## Edge cases the convert pipeline does NOT handle (deliberate choices)

- **"Radio Show -" pseudo-artists** (Armin's ASOT episodes tagged `album_artist = "Radio Show - …"`): we trust whatever tag the rip used. Workaround: re-tag source.
- **Classical "Best Of / Beethoven, Mozart, …" wrappers**: NOT coalesced into a single Various Artists comp. Per-composer artist folders are the better default.

Both documented in README's "Edge-case behaviours worth knowing" section.

## CLI flag reference

### `convert`

- `INPUT_DIR` (default `./input`)
- `OUTPUT_DIR` (default `./output`)
- `--format / -f auto|alac|mp3|aac` (default `auto`)
- `--bitrate / -b 192k|256k|320k` (default `256k`, ignored for ALAC)
- `--enrich/--no-enrich` (default: auto-probe)
- `--dry-run` (plan only, no writes)
- `--overwrite/--no-overwrite` (default off)
- `--remove-source/--no-remove-source` (default off)
- `--allow-lossy-recompress` (off; opt into lossy → lossy)
- `--workers / -w N` (default 2)
- `--cover-max-edge PX` (default 1000)
- `--acoustid-key TEXT` (or `MUSICKIT_ACOUSTID_KEY` env var)

### `library`

- `--audit` (run audit rules + show warnings)
- `--issues-only` (filter tree to flagged albums)
- `--fix` (apply deterministic fixes)
- `--fix --prefer-dirname` (rewrite tags to match dir, instead of dir to match tag)
- `--dry-run` (with `--fix`: print what would happen)

### `serve`

- `TARGET_DIR` (default `./output`)
- `--host` (default `0.0.0.0`)
- `--port` (default `4533`)
- `--user` (override `serve.toml`)
- `--password` (override `serve.toml`)

### `tui`

- `TARGET_DIR` (default unset — radio-only mode if omitted, OR Subsonic mode if state.json has creds)
- `--server URL` (Subsonic server URL; falls back to state.json)
- `--user U` / `--password P` (override state.json)

## TUI Subsonic-client mode

`musickit tui --server URL --user U --password P` connects to any Subsonic-compatible server. Three new mechanisms:

1. **`tui/subsonic_client.py`** — httpx-based read-only client (`ping`, `get_artists`, `get_artist`, `get_album`, `stream_url`, `cover_url`) plus a `build_index(client, on_progress=...)` walker that translates the API into `LibraryIndex` shape — same models the local-scan path produces, so widgets/formatters/advance-track all work unchanged.
2. **`LibraryTrack.stream_url: str | None = None`** — when set, `MusickitApp._play_current` passes the URL to `AudioPlayer.play()` instead of `track.path`. AudioPlayer already handles URLs (radio uses them), so the playback path is identical.
3. **state.json persistence** — after a successful `client.ping()` the CLI writes `{"subsonic": {"url": ..., "user": ..., "password": ...}}` to `~/.config/musickit/state.json`. Subsequent `musickit tui --server URL` launches drop the user/password flags; bare `musickit tui` resumes the last server when state has creds and no local DIR is given.

**Lazy loading is the default.** `build_index(client)` makes only 1 + N_artists calls (`getArtists` + `getArtist` per artist) and returns shell `LibraryAlbum`s with `subsonic_id` set but `tracks=[]`. When the user opens an album in the browser, `_hydrate_album_async` runs a Textual `@work(thread=True)` worker that calls `client.get_album(id)` and populates tracks in place; while it's in flight the tracklist shows a single `Loading tracks…` row. The hydrated tracks stay in memory for the rest of the session, so re-opening an album is instant. Pass `eager=True` to `build_index` for the old behaviour (every track pre-fetched at launch — useful if you want full-library shuffle without per-album hydration delay).

For an 800-album library, lazy mode cuts startup from ~900 calls to ~80 — well under a second over Tailscale.

Verified end-to-end against the local `musickit serve` TestClient: nine tests cover ping (success + failure), get_artists, lazy + eager build_index, hydrate_album_tracks (populates in place + idempotent), and stream_url / cover_url construction with auth params.

## Roadmap (current)

1. **Serve hardening**: no-op stubs for `scrobble`, `getStarred2`, `star`/`unstar`, `getRandomSongs`, `getPlaylists` (read-only first). Per-client transcoding (`?format=mp3` / `?maxBitRate=N`) only if a real client demands it.
3. **mDNS / Bonjour advertisement** for `musickit serve` so clients on the LAN auto-discover it.
4. **`musickit cover-pick`**: open musichoarders.xyz pre-filled per album for manual cover selection.
5. Fill in artist / release-group / per-track recording MBIDs from the existing MB query.
6. Folder-name fallback: strip arbitrary venue parens / live-edition annotations when no ALBUM tag is present.

## Operational state

- **167 tests passing**, lint + mypy + pyright clean.
- All seven refactor waves committed; six subcommands shipped (convert, inspect, library, retag, cover, tui, serve).
- Most recent commits (run `git log --oneline -15` for live state):
  - `2b6432a` feat: musickit serve — browsing + scan endpoints (Phase 2)
  - `b006b89` feat: musickit serve — Subsonic API skeleton (Phase 1)
  - `2017690` Extract PyAV container open + ICY metadata into tui/audio_io.py
  - `dcb791b` Trim tui/app.py: extract pure helpers (types, formatters, advance)
  - `99e9b47` Refactor library.py (467 lines) → library/ package
  - `dacbb96` Refactor pipeline.py (1099 lines) → pipeline/ package
  - `c44e8f3` Refactor metadata.py (974 lines) → metadata/ package
  - `b8a64b0` Refactor tui/app.py (1440 lines) → split into widgets/commands/state/app
  - `2022da3` Refactor cli.py (599 lines) → cli/ package
- The latest pre-doc-update Phase 3 commit will land alongside this MUSICKIT.md update.

## Resume-from-fresh-session checklist

1. `cd /Users/morteoh/Music/Audio` and `git status` — confirm clean tree.
2. `make lint && make test` — baseline (167 tests, lint + types clean).
3. `git log --oneline | head -20` — confirm recent commits include serve Phase 1/2/3.
4. Read `README.md` for user-facing docs; this `MUSICKIT.md` for context dump; `CLAUDE.md` for project rules (NO emojis is the big one).
5. Check the current task list — pending work is most likely `musickit tui` Subsonic-client mode (top of roadmap).
6. Ask the user what they want to work on next. If they say "continue serve", the next steps are the Phase 4 niceties (scrobble/star no-ops, mDNS) or the TUI client mode.
