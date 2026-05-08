# `musickit convert`

The heart of the project. Walks an input tree, groups audio files into albums, re-encodes via `ffmpeg`, writes clean tags + a normalised cover, lays the result out as `output/<Artist>/<YYYY> - <Album>/NN - <Title>.<ext>`.

```
uvx musickit convert INPUT_DIR OUTPUT_DIR [...flags]
```

Both directories are required (no defaults — `uvx`-installed runs anywhere on the filesystem and silent `./input` / `./output` magic would do the wrong thing). `INPUT_DIR` must exist; `OUTPUT_DIR` is created if missing. `--format` defaults to `auto`.

## What runs per album

Roughly in order:

1. **Discover** — walks the input root, groups audio files by leaf-with-tracks, merges multi-disc layouts (`CD1`/`CD2`, `Disc 1`/`Disc 2`, `Album (CD1)`/`Album (CD2)`, etc.).
2. **Read source tags** — mutagen via `metadata.read_source(path)` per track. Format-specific readers for FLAC / MP3 / MP4; generic fallback for OGG / Opus / WAV.
3. **Pre-fill from filename** — if a track is missing title or artist, `_parse_filename_for_va` extracts `Artist - Title` from `NN. Artist - Title.ext` style filenames. Critical for tagless rips.
4. **AcoustID lookup** (if `--acoustid-key` set, or `[acoustid].api_key` in `musickit.toml`) — Chromaprint fingerprint for tracks still missing title/artist, query [acoustid.org](https://acoustid.org). Off by default — requires a free user-supplied API key.
5. **Summarise** — majority-vote across tracks for album / album_artist / year / genre. Disc-1 biased so bonus-disc tags don't pollute the album-name pick on multi-disc releases.
6. **Folder fallback** — when no ALBUM tag exists, parse the directory name. Strips codec/quality scene tags (`[FLAC]`, `[16Bit-44.1kHz]`), edition annotations (`(Remastered)`, `(Deluxe Edition)`, `(2018 Reissue)`), keeps live-album annotations (`(Live in Madrid)`) since they're real distinctions.
7. **Resolve output paths** — `naming.artist_folder` + `naming.album_folder` + `naming.track_filename`. VA → `Various Artists`. Multi-disc → `01-NN - Title.m4a`.
8. **Cover** — `cover.collect_candidates` gathers every embedded picture across the album's tracks plus `folder.jpg`/`cover.jpg`/`front.jpg` siblings. Under `--enrich`, also fetches from MusicBrainz / Cover Art Archive. `cover.pick_best` picks the highest pixel area; `cover.normalize` resizes to `--cover-max-edge` (default 1000) JPEG quality 90.
9. **Collision check** — refuse to overwrite an existing output album dir unless `--overwrite`. Two source albums normalising to the same output path get the second one skipped + warned.
10. **Encode** — staging dir `<out_dir_parent>/.<out_dir_name>.staging/`, `ThreadPoolExecutor` per-track. `convert.encode` (or `remux_to_m4a` / `copy_passthrough` when `auto_resolve` says copy is safe). `metadata.write_tags` writes the full target tag set per track.
11. **Atomic swap** — rename old `out_dir` to `.<name>.backup`, rename `staging` into place, drop `backup`. Mid-encode failure leaves the prior album intact.
12. **Source removal** (if `--remove-source`) — `shutil.rmtree(_input_footprint(album_dir))` per album, on success only. Footprint computation handles wrapped multi-disc layouts.
13. **Report** — `AlbumReport` per album; final summary table.

## `--format` matrix

| Value | Codec | Container | Lossy? | Bitrate | When to pick it |
|---|---|---|---|---|---|
| `auto` (default) | per-source dispatch | mostly `.m4a` | varies | varies | The smart default — see below. |
| `alac` | Apple Lossless | `.m4a` | no — bit-perfect | ~600–1100 kbps for CD, ~1500–3000 kbps hi-res | Archival / library master copy. Round-trips back to FLAC with no loss. |
| `aac` | AAC-LC (ffmpeg native) | `.m4a` | yes | VBR around `--bitrate` (default 256k) | Best per-byte sound quality among lossy options. **256k AAC matches Apple Music streaming.** |
| `mp3` | MP3 (libmp3lame) | `.mp3` | yes | VBR around `--bitrate` | Maximum compatibility — older car stereos, embedded systems, anything that doesn't grok MP4 containers. |

### `--format auto` (default)

Targets a **uniform `.m4a` library at 256 kbps AAC**. Per-source dispatch:

| Source codec | Action | Reason |
|---|---|---|
| FLAC, WAV (lossless) | encode → 256k AAC m4a | One lossy pass from a lossless source — Apple Music quality, ~24% the size. |
| AAC `.m4a` | stream-copy into clean re-tagged m4a | Already AAC, free pass; preserves quality. |
| ALAC `.m4a` | encode → 256k AAC m4a | Lossless source → single lossy pass, same as FLAC. |
| MP3, OGG, Opus, AAC, other lossy | encode → 256k AAC m4a | One-time tandem encode for library uniformity. The cost is below the audibility threshold on consumer playback gear (and fully masked by Bluetooth, which re-encodes at 256k AAC anyway); the win is one extension, one tag schema, metadata visible everywhere. |

To skip the tandem encode and keep lossy sources at full bitrate, use `--format aac` without `--allow-lossy-recompress`: lossy sources fall back to ALAC m4a (lossless wrapper of the lossy bytes; bigger but no further degradation). `--format alac` forces ALAC m4a for every track regardless of source.

### Size and quality, in practice

Approximate ratios versus an ALAC master (~1000 kbps average for a typical mixed 16/24-bit library):

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

### Container nuance

`.m4a` is the MP4 container — the codec inside is either ALAC (lossless) or AAC (lossy). Both resolve to the same MP4 tag atoms (`\xa9nam` title, `\xa9ART` artist, `aART` album artist, `trkn`/`disk` track/disc tuples, `covr` cover, `cpil` compilation, `----:com.apple.iTunes:LABEL` etc.). Plain `.mp3` files use ID3v2.4 frames (`TIT2`, `TPE1`, `TPE2`, `TALB`, `TRCK`, `TPOS`, `TCMP`, `APIC`, plus `TXXX` for replaygain and MusicBrainz IDs).

MP3 sources are transcoded to AAC under `auto` rather than remuxed into MP4 — Finder doesn't display tags reliably for MP3-in-MP4 hybrids, so the library stays uniform-AAC instead.

## Online enrichment (`--enrich` / `--no-enrich`)

**On by default when an internet connection is available.** A fast TCP probe to MusicBrainz at startup decides; if it fails the run continues with local-only cover sourcing. `--enrich` forces it on (skips the probe — useful on networks where the probe is blocked but HTTP works); `--no-enrich` disables it entirely.

When active, each album runs:

1. **MusicBrainz release search** at `https://musicbrainz.org/ws/2/release/` keyed on the album title + album artist + track count. The top result is accepted only when its match score is ≥ 90, which avoids false positives on common compilation/best-of titles. The response carries `artist-credit` and `release-group` inline, so we populate `MusicBrainzIds.album_id`, `artist_id`, `release_group_id` in a single round trip.
2. **Cover Art Archive** at `https://coverartarchive.org/release/<MBID>/front-1200` is queried for the resolved release MBID.

The fetched cover joins the local candidates and the picker keeps the highest-area image. The picker **never downgrades** — if the online result is smaller than what's already on disk, we keep local. The resolved release MBID is written to the output tags as `----:com.apple.iTunes:MusicBrainz Album Id` (MP4) / `TXXX:MusicBrainz Album Id` (MP3).

Both calls go through a polite client: a 1 req/sec throttle per host, a descriptive User-Agent, and 15-second timeouts. Errors are non-fatal — the album just falls back to the offline candidate and a warning lands in the per-album notes column.

## All flags

- `INPUT_DIR` (required, must exist)
- `OUTPUT_DIR` (required; created if missing)
- `--format / -f auto|alac|mp3|aac` (default `auto`)
- `--bitrate / -b 192k|256k|320k` (default `256k`, ignored for ALAC)
- `--enrich/--no-enrich` (default: auto-probe, on if reachable)
- `--dry-run` — plan only, no files written
- `--overwrite/--no-overwrite` — default off; existing albums are preserved + the run merges into existing output
- `--remove-source/--no-remove-source` — default off; when on, deletes each input album dir on successful convert
- `--allow-lossy-recompress` — opt into MP3→AAC tandem encode under `--format aac`
- `--workers / -w N` (default 4; each worker spawns ffmpeg which is itself multi-threaded)
- `--cover-max-edge PX` (default 1000)
- `--acoustid-key TEXT` (or `MUSICKIT_ACOUSTID_KEY` env var, or `[acoustid].api_key` in `~/.config/musickit/musickit.toml`; off by default)

`-v`/`--verbose` is a top-level callback flag — works in any position (`musickit -v convert …` or `musickit convert … --verbose`).
