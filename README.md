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
```

`make lint`, `make test`, `make coverage` for the standard dev loop. `ffmpeg` and `ffprobe` must be on `$PATH`.

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

## Project layout

```
src/musickit/
  __init__.py     __main__.py      cli.py
  pipeline.py     discover.py      metadata.py
  naming.py       convert.py       cover.py
  enrich/         _http.py         musicbrainz.py
                  coverart.py      musichoarders.py
tests/            unit tests covering naming, discovery, metadata round-trips, convert + tag dispatch, atomic album writes, AUTO codec dispatch, and the enrich providers (with mocked HTTP).
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

Convert pipeline (this CLI's current scope) is complete. Planned future commands:

- **`musickit serve`** — local audio server. The most useful target is a **Subsonic-compatible API** so off-the-shelf clients can connect: Symfonium / DSub / Substreamer (mobile), Sonixd / Supersonic (desktop), Jellyfin Finamp, Airsonic web players, etc. FastAPI + a SQLite catalog scanned from the converted output dir; HTTP range requests for seeking; transcoding-on-the-fly via the existing `convert.encode` for unsupported client codecs. mDNS/Bonjour advertisement so it shows up on the LAN automatically.
- **`musickit ui` / `musickit tui`** — playback + browser pointed at the output dir. Two viable shapes:
  - **TUI** with [Textual](https://textual.textualize.io/): three-pane (artist / album / track) navigation, mpv subprocess for playback, rich-rendered now-playing footer. Cheap to build, runs over SSH, fits the project's Python-only stack.
  - **Web UI** served alongside `musickit serve`: same Subsonic backend, a small Vue/htmx frontend. Heavier but reusable from any browser.

  Likely path: TUI first (shares no infrastructure with `serve`, ships independently), then Web UI as a follow-on once `serve` exists.

### Convert-pipeline follow-ups (smaller)

- `musickit cover-pick`: open musichoarders.xyz pre-filled per album for manual cover selection (semi-automated workflow per their integration policy).
- Better bonus-disc handling for the SOAD-style layout (parent owns audio + `Disc N` subfolders that aren't pure duplicates).
- Optional `chromaprint` / AcoustID lookup as a stronger MusicBrainz match path for albums with non-standard titles.
- Folder-name fallback: strip arbitrary venue parens / live-edition annotations when no ALBUM tag is present.
