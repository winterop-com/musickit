# musickit

A `uv`-managed Python 3.13 CLI that converts arbitrary audio rips (FLAC, MP3, M4A, WAV, OGG, OPUS) into a clean, tagged, organised library.

## What it does, end to end

1. Walks an input tree and groups audio files into albums (one album per leaf directory). Multi-disc layouts get merged: `CD1`/`CD2`, `CD-1`/`CD-2`, `Disc 1`/`Disc 2`, `Album (CD1)`/`Album (CD2)`, `CD2 (Bonus Live CD)` — all detected by shared-prefix grouping.
2. Reads source tags via `mutagen` (FLAC Vorbis comments, ID3v2.x, MP4 atoms) plus filename fallback (`NN - Title.flac`, `NN. Artist - Title.flac` for VA, `D-NN. Title.flac` for filename-encoded multi-disc).
3. Re-encodes audio with `ffmpeg`, keeping a single audio stream — embedded picture is stripped here and re-embedded by the tagger using a single normalized cover for the whole album.
4. Picks a cover from this fallback chain (highest pixel area wins, with provider-order tiebreak):
   - embedded picture in any source track,
   - `cover|folder|front|albumart.{jpg,jpeg,png,webp}` next to the tracks,
   - under `--enrich`, the front cover from the Cover Art Archive (resolved via MusicBrainz release MBID).
5. Writes a clean tag set to the output: title, artist, album artist, album, year, genre, track number `N/total`, disc number `N/total`, BPM, lyrics, label, catalog number, replaygain values, MusicBrainz IDs (under `--enrich`), and the embedded cover. The compilation flag (MP4 `cpil` / ID3 `TCMP`) is set when the album artist matches `VA`/`Various`/`Various Artists` aliases — those albums also collapse to the canonical `Various Artists` folder.
6. Lays the result out as `output/<Artist>/<YYYY> - <Album>/NN - <Title>.<ext>` (or `DD-NN - <Title>.<ext>` for multi-disc). Year-prefixed for chronological sort. Slash/colon/etc. sanitized for cross-FS portability; `R.E.M.`-style trailing dots preserved.

## Usage

`musickit` exposes six subcommands; this section covers `convert` (the heart of the project). The library / TUI / serve commands have their own sections further down.

```bash
uv sync
uv run musickit convert ./input ./output                          # default: --format auto (per-source dispatch)
uv run musickit convert ./input ./output --format alac            # archival: every track encoded to ALAC m4a
uv run musickit convert ./input ./output --format aac --bitrate 320k
uv run musickit convert ./input ./output --format mp3             # libmp3lame, 256k by default
uv run musickit convert ./input ./output --enrich                 # MusicBrainz + Cover Art Archive
uv run musickit convert ./input ./output --verbose                # per-track log lines instead of progress bar
uv run musickit convert ./input ./output --dry-run                # plan only, no files written
uv run musickit convert ./input ./output --format aac --allow-lossy-recompress   # opt into lossy → lossy

uv run musickit inspect path/to/file.m4a                          # tag + cover summary
uv run musickit library ./output --audit                          # Artist→Album→Track tree + warning rules
uv run musickit library ./output --fix                            # apply deterministic fixes (MB year, dir↔tag rename)
uv run musickit retag path/to/album --year 1976 --album "Arrival" # in-place tag overrides
uv run musickit cover  path/to/album cover.jpg                    # retrofit cover art
uv run musickit tui    ./output                                   # Textual TUI (or omit DIR for radio-only)
uv run musickit serve  ./output                                   # Subsonic-compatible HTTP server
```

`make lint`, `make test`, `make coverage` for the standard dev loop. `ffmpeg` and `ffprobe` must be on `$PATH` for `convert`; `serve` and `tui` only need them indirectly (when re-encoding or playing files that need the codec).

## Output formats

Four `--format` choices. **`auto` is the default** and gives the best size/quality balance for a mixed library; the explicit codec names force one codec for everything.

### `--format auto` (default)

Targets a **uniform `.m4a` library at 256 kbps AAC**. Every track ends up `.m4a` (with AAC inside) so Finder, Music.app, iTunes, and every modern player display tags consistently. Override with `--format alac` to force a lossless ALAC archive instead.

| Source codec | Action | Reason |
|---|---|---|
| FLAC, WAV (lossless) | encode → 256k AAC m4a | One lossy pass from a lossless source — Apple Music quality, ~24% the size. |
| AAC `.m4a` | stream-copy into clean re-tagged m4a | Already AAC, free pass; preserves quality. |
| ALAC `.m4a` | encode → 256k AAC m4a | Lossless source → single lossy pass, same as FLAC. |
| MP3, OGG, Opus, AAC, other lossy | **encode → 256k AAC m4a** | One-time tandem encode for library uniformity. The cost is below the audibility threshold on consumer playback gear (and fully masked by Bluetooth, which re-encodes at 256k AAC anyway); the win is one extension, one tag schema, metadata visible everywhere. |

To skip the tandem encode and keep lossy sources at full bitrate, use `--format aac` without `--allow-lossy-recompress`: lossy sources fall back to ALAC m4a (lossless wrapper of the lossy bytes; bigger but no further degradation). `--format alac` forces ALAC m4a for every track regardless of source.

### Forced codec modes

| Format | Codec | Container | Lossy? | Bitrate | When to pick it |
|---|---|---|---|---|---|
| `alac` | Apple Lossless | `.m4a` | **No** — bit-perfect | ~600–1100 kbps CD, ~1500–3000 kbps hi-res | Archival / library master copy. Round-trips back to FLAC with no loss. |
| `aac` | AAC-LC (ffmpeg native) | `.m4a` | Yes | VBR around `--bitrate` target (default 256k) | Best per-byte sound quality of the lossy options. **256k AAC matches Apple Music streaming** and is transparent for nearly all listeners on consumer gear. |
| `mp3` | MP3 (libmp3lame) | `.mp3` | Yes | VBR around `--bitrate` target | Maximum compatibility — older car stereos, embedded systems, anything that doesn't grok MP4 containers. |

When you force a lossy codec (`--format aac` or `--format mp3`) and the source is itself lossy, the per-track guard kicks in and silently falls back to ALAC for that track to avoid a tandem encode. Pass `--allow-lossy-recompress` to override and force the lossy → lossy transcode.

Container nuance worth knowing: `.m4a` is the MP4 container — the codec inside is either ALAC (lossless) or AAC (lossy). Both resolve to the same MP4 tag atoms (`\xa9nam` title, `\xa9ART` artist, `aART` album artist, `trkn`/`disk` track/disc tuples, `covr` cover, `cpil` compilation, `----:com.apple.iTunes:LABEL` etc.). Plain `.mp3` files use ID3v2.4 frames (`TIT2`, `TPE1`, `TPE2`, `TALB`, `TRCK`, `TPOS`, `TCMP`, `APIC`, plus `TXXX` for replaygain and MusicBrainz IDs). MP3 sources are transcoded to AAC under `auto` rather than remuxed into MP4 — Finder doesn't display tags reliably for MP3-in-MP4 hybrids, so the library stays uniform-AAC instead.

### Size and quality, in practice

Approximate ratios versus an ALAC master (~1000 kbps average for a typical mixed 16-bit/24-bit library):

| Codec / bitrate | Size vs ALAC | Audibility vs lossless |
|---|---|---|
| ALAC | 100% | identical (lossless) |
| AAC 320k | ~32% | transparent on consumer gear |
| **AAC 256k** | **~24%** | **transparent for nearly all listeners** |
| AAC 192k | ~18% | mostly transparent; rare audible artefacts |
| MP3 320k | ~30% | transparent for most listeners |
| MP3 256k | ~24% | usually transparent |
| MP3 192k | ~18% | audible on critical listening |

Per byte, AAC sounds noticeably better than MP3 at the same bitrate — that's why iTunes Plus and Apple Music settled on **256k AAC** as the lossy default rather than 320k MP3.

## Online enrichment (`--enrich` / `--no-enrich`)

**On by default when an internet connection is available.** A fast TCP probe to MusicBrainz at startup decides; if it fails the run continues with local-only cover sourcing. `--enrich` forces it on (skips the probe — useful on networks where the probe is blocked but HTTP works); `--no-enrich` disables it entirely. When active, each album runs:

1. **MusicBrainz release search** (`https://musicbrainz.org/ws/2/release/`) keyed on the album title + album artist + track count. The top result is accepted only when its match score is ≥ 90, which avoids false positives on common compilation/best-of titles.
2. **Cover Art Archive** (`https://coverartarchive.org/release/<MBID>/front-1200`) is queried for the resolved release MBID.

The fetched cover joins the local candidates and the picker keeps the highest-area image. The picker **never downgrades** — if the online result is smaller than what's already on disk, we keep local. The resolved release MBID is written to the output tags as `----:com.apple.iTunes:MusicBrainz Album Id` (MP4) / `TXXX:MusicBrainz Album Id` (MP3). Artist / release-group / per-track recording IDs are not yet resolved — the tag schema supports them and `MusicBrainzIds` carries the fields, but the current MB query only returns the release-level MBID. Adding a follow-up query to fill in the others is on the roadmap.

Both calls go through a polite client: a 1 req/sec throttle per host, a descriptive User-Agent, and 15-second timeouts. Errors are non-fatal — the album just falls back to the offline candidate and a warning lands in the per-album notes column.

The third provider, `musichoarders.xyz`, is intentionally **not** scraped under `--enrich`. Its integration policy forbids fully automated artwork retrieval; the supported path is a semi-automated browser pre-fill (`?artist=…&album=…`) for manual user pick. A future `musickit cover-pick` subcommand will plug into that flow; the URL builder lives at `musickit.enrich.musichoarders.build_search_url`.

## Browsing + auditing the converted library — `musickit library`

```bash
uv run musickit library ./output                       # Artist → Album → Track tree (rich.Tree)
uv run musickit library ./output --audit               # tree + per-album warnings
uv run musickit library ./output --issues-only         # show only flagged albums
uv run musickit library ./output --fix                 # apply deterministic fixes
uv run musickit library ./output --fix --prefer-dirname --dry-run    # invert tag/dir resolution
```

Audit rules: missing/mixed year, no cover, low-res cover (<500×500), mixed `album_artist`, scene residue in dir or album tag, scene-domain artist dirs, `Unknown Artist`, tag/path mismatch, track gaps. `--fix` resolves the deterministic ones in place: missing-year via MusicBrainz, and tag/path-mismatch by either renaming the dir to match the tag (default) or rewriting tags to match the dir (`--prefer-dirname`).

## Terminal UI — `musickit tui`

A Textual TUI for browsing + playing the converted library, plus a curated radio section.

```bash
uv run musickit tui ./output           # library + radio mode
uv run musickit tui                    # radio-only (no library scan)
```

Three-pane layout: sidebar (stats + Artist→Album browser tree), main (now-playing meta + 24-band FFT visualizer + progress + track list), bottom keybar. Decoder is in-process via PyAV; output via sounddevice/PortAudio (both ship pip wheels — no `brew install` needed). Radio plays Icecast/Shoutcast streams with live ICY metadata (`StreamTitle` updates the now-playing block).

Keybindings: `↑`/`↓` navigate, `Enter`/`Space` play/pause, `n`/`p` next/prev, `</>` seek ±5s, `+`/`-` volume, `s` shuffle, `r` repeat, `f` fullscreen visualizer, `Tab` focus switch, `Ctrl+R` rescan, `?` help panel, `Ctrl+P` command palette, `q` quit.

## Streaming the library to phones / other rooms — `musickit serve`

A Subsonic-compatible HTTP server. Any modern Subsonic client connects: **Symfonium / Tempo / Ultrasonic** (Android), **Amperfy / play:Sub / Substreamer** (iOS), **Feishin / Supersonic** (desktop). The spec implementation tracks [OpenSubsonic v1.16.1](https://opensubsonic.netlify.app/docs/api-reference/).

```bash
uv run musickit serve ./output --user mort --password secret
```

On startup the banner prints both LAN and Tailscale URLs:

```
musickit serve — Subsonic API for /Volumes/T9/Output
  bind: 0.0.0.0:4533
  LAN:  http://192.168.1.42:4533
  Tailscale: http://my-mac.tail-scale.ts.net:4533

scanning library…
  142 artists, 318 albums, 4521 tracks
```

Point Symfonium / Amperfy at the URL, sign in, browse + play. Range requests are honoured so seeking works mid-track. Cover art comes from sidecar files (`cover.jpg` / `folder.jpg` / `front.jpg`) first, embedded picture as fallback, with optional Pillow resize via `?size=`.

Defaults:

| Flag | Default | Notes |
|---|---|---|
| `--host` | `0.0.0.0` | All interfaces — required for Tailscale (loopback isn't reachable). Auth is always enforced; opt into `127.0.0.1` if you want LAN-blind. |
| `--port` | `4533` | Navidrome's default; clients pre-fill it. |
| `--user` / `--password` | from `~/.config/musickit/serve.toml` | CLI flags override the file. |

Endpoints implemented (under `/rest/`):

| Group | Endpoints |
|---|---|
| System | `ping`, `getLicense`, `getMusicFolders` |
| Browsing | `getArtists`, `getArtist`, `getAlbum`, `getAlbumList2` (alphabeticalByName/Artist, random, byYear, byGenre), `getSong`, `getIndexes` |
| Search | `search3`, `search2` (multi-token AND, case-insensitive substring) |
| Media | `stream` (with `Accept-Ranges: bytes`), `download`, `getCoverArt` (with optional `?size=` Pillow resize) |
| Library | `startScan` (background-thread rescan), `getScanStatus` |

Auth: plain `?p=`, `enc:<hex>`, and salted-token `?t=md5(password+salt)&s=salt` — all three forms in the Subsonic spec.

### Tailscale story

Bind to `0.0.0.0`, install Tailscale on the server + your phone, leave the rest to MagicDNS:

1. `tailscale up` on the Mac.
2. `tailscale ip -4` (or read the banner) for the tailnet IP — or use the MagicDNS hostname (`<machine-name>.<tailnet>.ts.net`).
3. In Symfonium / Amperfy: server URL = `http://<that-hostname>:4533`, user + password from `serve.toml`.

You're now reachable from any device on your tailnet, anywhere on the internet, no port forwarding, no HTTPS to set up — Tailscale's WireGuard tunnel handles encryption. This was the whole point of binding to `0.0.0.0` rather than `127.0.0.1`.

## Project layout

```
src/musickit/
  __init__.py        __main__.py
  cli/               typer entry — one file per subcommand (convert, cover, inspect, library, retag, serve, tui)
  convert.py         ffmpeg dispatch (encode / remux / copy_passthrough)
  cover.py           cover-source candidates + pick_best + normalise
  discover.py        walk input → list[AlbumDir] (with multi-disc merge)
  library/           Artist→Album→Track index of the converted output
    models.py        scan.py        audit.py        fix.py
  metadata/          tag read/write — FLAC / MP3 / MP4 / generic
    models.py        album.py       read.py         write.py        overrides.py
  naming.py          filesystem-safe folder + filename builders
  pipeline/          orchestrator — discover → cover → convert → tag → swap
    run.py           album.py       track.py        report.py       progress.py
    filenames.py     disc.py        dedupe.py       footprint.py    acoustid.py
  radio.py           curated internet-radio station list (NRK defaults + user TOML)
  serve/             Subsonic-compatible HTTP server (FastAPI)
    app.py           auth.py        config.py       ids.py          index.py
    payloads.py      covers.py
    endpoints/       system.py  browsing.py  media.py  search.py  scan.py
  tui/               Textual TUI
    app.py           widgets.py     player.py       audio_io.py
    advance.py       commands.py    formatters.py   state.py        types.py
  enrich/            _http.py       musicbrainz.py  coverart.py
                     musichoarders.py  acoustid.py  __init__.py
tests/               167 tests covering naming, discovery, metadata round-trips, convert + tag dispatch, atomic album writes, AUTO codec dispatch, enrich providers (mocked HTTP), library audit, TUI player, and the full serve API surface (auth + envelope + browsing + media + search + scan).
```

`pyproject.toml` and `Makefile` follow the same shape as `~/dev/chap-sdk/chapkit`: ruff (E/W/F/I/D, google docstrings, py313, line-length 120), mypy strict-ish, pyright `strict` with the same `report*` softeners, pytest + coverage.

## Edge-case behaviours worth knowing

### "Radio Show -" pseudo-artists

Some rips deliberately tag the `album_artist` as a *programme* rather than a *person* — most commonly Armin van Buuren's *A State Of Trance* weekly show, where the rippers set `album_artist = "Radio Show - A State Of Trance"` on every episode. The convert trusts what's tagged, so those episodes land under `output/Radio Show - A State Of Trance/2025 - A State Of Trance 1254 (...)/` rather than mixed into `Armin van Buuren/`. This keeps weekly radio shows from polluting the main-artist library, but means a search for the actual artist won't find them.

If you'd rather have them under the real artist, either:
- **Re-tag the source** (`kid3`, `Mp3tag`, `mutagen`-cli) before converting — this is the canonical fix because the tag is the source of truth everywhere downstream.
- **Override at convert time** — currently not implemented, but a `--artist-override "Radio Show - A State Of Trance=Armin van Buuren"` flag would slot in without touching source files. Roadmap.

### Classical-style compilations (`Best Of / Beethoven, Mozart, …`)

A wrapper folder with one sub-folder per composer — each sub-folder full of tracks tagged only with the composer name, no `ALBUM` tag — produces **one output album per composer-folder**, not a single merged "Various Artists" comp. We tried the merge once and concluded the per-composer artist folders were the better default (each composer is the artist; the wrapper "best-of" name isn't really meaningful as an album). If you want them coalesced under `Various Artists/<wrapper-name>/` instead, re-tag the source files with a shared `ALBUM` tag and `album_artist = "Various Artists"` and the existing pipeline does the right thing.

### MP3-in-MP4 deliberately avoided

When a source MP3 is processed under `--format auto`, the output is **transcoded to AAC** rather than stream-copied into an `.m4a` container. MP3-in-MP4 is a valid combination per the MP4 spec and `mutagen` reads its tags fine, but Finder / Music.app's metadata pipeline shortcuts based on the codec field and won't display tags reliably. Transcoding loses a tiny amount of audio fidelity (transparent on Bluetooth playback) in exchange for a tag schema every consumer can read.

## Roadmap

Convert + library + retag/cover + TUI + Subsonic server are all shipped. Remaining work, in rough priority order:

- **`musickit tui` as a Subsonic client**, not just a local-library player — point it at any Subsonic-compatible server (including its own `musickit serve`) and browse/play remotely. Lets the same TUI work over Tailscale from a laptop without mounting the library. Adds a `--server URL --user USER --password PWD` triplet (or stored creds in `~/.config/musickit/state.json`); when present, `library.scan()` is replaced by Subsonic API calls and `AudioPlayer.play()` is given the `/rest/stream?id=...` URL (PyAV already plays HTTP).
- **Serve hardening**: `scrobble` / `getStarred2` / `star` no-op stubs (clients pre-fetch them and log errors otherwise), `getRandomSongs`, `getPlaylists` (read-only first), per-client transcoding (`?format=mp3` / `?maxBitRate=N`) only if a real client demands it.
- **mDNS / Bonjour advertisement** for `musickit serve` so clients on the LAN auto-discover it without typing the URL.
- **`musickit cover-pick`**: open musichoarders.xyz pre-filled per album for manual cover selection (semi-automated workflow per their integration policy).
- Fill in artist / release-group / per-track recording MBIDs from the existing MusicBrainz query (~3 hours).
- Folder-name fallback: strip arbitrary venue parens / live-edition annotations when no ALBUM tag is present.
- Optional `chromaprint` / AcoustID lookup as a stronger MusicBrainz match path for albums with non-standard titles.
