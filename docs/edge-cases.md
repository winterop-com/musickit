# Edge cases

Every weirdness here came from a real input that exposed it. Most are handled by the convert pipeline; a few are deliberate non-handlings explained at the bottom.

## Multi-disc folder layouts

The discover step merges siblings with shared prefixes:

| Layout | Detected as |
|---|---|
| `Album/CD1`, `Album/CD2` | one album, 2 discs |
| `Album/CD-1`, `Album/CD-2` | one album |
| `Album/CD 1`, `Album/CD 2` | one album |
| `Album/Disc 1`, `Album/Disc 2` | one album |
| `Album/Disk3` | (treated as disc 3 if siblings exist) |
| `Album (CD1)/`, `Album (CD2)/` | one album, shared-prefix style |
| `Album/CD2 (Bonus Live CD)` | merges with `CD1` even with trailing text |

Single `Album/CD1/` (no CD2 sibling) is **not** promoted to multi-disc — treated as a regular single-disc album.

Mixed-content parents (Ultimate Queen layout: a folder with both `CD1/`/`CD2/` AND bare albums as siblings) only merge the matching-prefix disc pairs; bare albums stay as-is.

Parent-with-audio + disc subfolders (System Of A Down self-titled with duplicate `Disc 1/`/`Disc 2/` subfolders inside the album folder containing the same tracks): drops the disc subfolders, uses the parent's top-level tracks as one album.

## Filename-encoded multi-disc

Some rips put the disc + track in the filename rather than a CD subfolder:

```
Zara Larsson — So Good (Deluxe)/
  1-01. Ain't My Fault.flac
  1-02. I Would Like.flac
  ...
  2-01. So Good.flac
  2-02. Lush Life.flac
```

`_maybe_apply_filename_disc_track` detects + applies disc/track from the filename pattern when no folder-disc structure exists. Triggers only when ≥2 distinct disc numbers appear (so single-disc `01-NN` doesn't accidentally trip).

### Continuous numbering across discs

Mega-comp convention: disc 1 has tracks 1-9, disc 2 has 10-18, etc. — track numbers continue rather than restart per disc.

```
A State Of Trance Classics Vol. 7/
  01-01 - Chicane - Offshore.m4a
  01-02 - Above & Beyond - ...
  ...
  01-09 - G&M Project - ...
  02-10 - BT - Flaming June.m4a       # disc 2 starts at track 10
  02-11 - Marcel Woods - ...
  ...
  02-18 - Peter Martijn Wijnia - ...
  03-19 - Marco V - GODD.m4a          # disc 3 starts at 19
  ...
```

The convert pipeline detects this (each disc's min track == previous disc's max + 1) and resets to per-disc 1..N in the output tags. Otherwise Subsonic / Music.app would show "track 10" of "1 track" on disc 2, which displays weirdly.

## Scene-encoded `DTT` track numbers

Now! / Absolute Music / Billboard compilations encode multi-disc structure into a 3-digit track number prefix on the filename (`101_artist_-_title.mp3` = disc 1 track 1, `218_artist_-_title.mp3` = disc 2 track 18). The MP3 `track` tag often carries the in-disc number; the filename carries the encoded form. We read the filename because the tag is unreliable.

Trigger conditions (all required, conservative):

- `discover` did NOT already merge this album from disc subfolders.
- Every track's filename starts with a 3-digit number (100-999).
- The set of `tn // 100` has ≥2 unique values.
- Each disc cluster has ≥3 tracks.

A 100-track regular album with one track numbered 100 won't accidentally trigger this.

## Various-Artists detection

Detected as compilation when:

- `album_artist` is a VA alias (`VA`, `Various`, `Various Artists`, `V.A.`, `V/A`, `Various Artist`, `Verschillende Artiesten`, etc.)
- The per-track artist majority is itself a VA alias (rips that leave album_artist empty but stamp every track artist as `VA`)
- No `album_artist` and tracks span multiple different artists

Compilations route to `Various Artists/` and set `cpil` (MP4) / `TCMP` (ID3) compilation flag.

### Tagless VA filename parsing

Filename `01 - VA - Artist - Title.flac` has a leading `VA -` segment. The filename parser splits out the real per-track artist; summary detects compilation by VA-marker artist_fallback even if `album_artist` is empty.

### Tagless 100-track VA mix

Real example: a `7Os8Os9Os` compilation with 100 MP3s named `NN. Artist - Title.mp3` and **no tags at all**. Filename pre-fill in `_process_album` populates per-track artists before summarise → distinct artists triggers compilation flag → output lands under `Various Artists/`.

## Disc-suffix in album tags

```
Roses (CD1) → Roses
Are You Ready: Best Of AC/DC [CD1] → Are You Ready: Best Of AC/DC
Roses (CD2) Live In Madrid → Roses (Cranberries layout — middle-of-string disc marker)
Echoes (1) → Echoes (bare-paren disc index)
```

`clean_album_title` strips them, including the bare-trailing `(1)`/`(2)` form.

## Disc-1 bias in album-name vote

Multi-disc albums where bonus discs have polluted tags (e.g. `Album (CD2) Live In Madrid`) would otherwise win majority count over disc 1's clean `Album` tag. The summary biases toward disc 1 — disc 1 tracks vote first; only fall back to all tracks if disc 1 had no consensus.

## Folder name junk

Stripped from the directory name when used as ALBUM fallback:

- Codec / quality bracketed tags: `[FLAC]`, `[16Bit-44.1kHz]`, `[lossless]`
- Year: `(2012)` (extracted, not just dropped)
- VA prefix: `VA - `, `Various - `
- Scene-domain tags: `[nextorrent.com]`, `[example.org]` — limited to known TLDs so we don't strip `[Live]` / `[PMEDIA]` / catalog numbers

Edition annotations (added in a later commit):

- `(Remastered)`, `(Remastered 2009)`, `(2009 Remaster)`
- `(Deluxe Edition)`, `(Super Deluxe Edition)`, `(Expanded Edition)`
- `(40th Anniversary Edition)`, `(10th Anniversary)`
- `(Bonus Tracks)`, `[Bonus Disc]`
- `(2018 Reissue)`, `(Reissue)`
- `(Special Edition)`, `(Limited Edition)`, `(Collector's Edition)`

## Filesystem-safe sanitisation

- `/`, `:`, `*`, `?`, `"`, `<`, `>`, `|` all replaced with `_`
- `R.E.M.` trailing dots preserved (single-letter acronyms — distinct from disc-marker `(1).`)
- NFC unicode normalisation
- ≤180-byte component cap (some filesystems cap at 255; we leave headroom)

Smart quotes / em-dashes (Sting `1984–1994`): preserved verbatim.

## Filename collisions

Two tracks with same track-no + title in one album → auto-suffix `(2)`, `(3)`. Reserves the FINAL filename, not just the planned one, so a third track titled `Same (2)` chains to `(3)` rather than colliding with the auto-renamed second.

## Output dir collisions

Two source albums normalising to the same output path → skip second + warn in the summary. Surfaces in `--dry-run` too. Reservation happens BEFORE encode so dry-run and real-run agree on what would happen.

## Source-side dedupe

Some rip groups ship every track twice under different filename conventions:

```
01. Artist - Title.flac
01 Title.flac           ← same content, different filename style
```

Match on `(disc_no, track_no, title.casefold(), artist.casefold())` + duration within 0.5s. Without the duration check, a remix and its original at the same `track_no` would falsely dedupe; with it, only true duplicates collapse.

## Atomic per-album writes

Hidden `.staging` sibling dir, swap on success, no half-replaced albums on mid-encode failure. Per-track failures caught at thread level, propagate to album-level `error` (album fails in summary, CLI exits non-zero, prior album content preserved by atomic swap).

## Cover corruption

`_measure` returns `(0, 0)` on Pillow failure → candidate dropped from the picker. `cover.normalize()` exception → fall through to next-best candidate. CAA HTTP 5xx (Internet Archive CDN flaky) → caught + warning + fall back to local cover. Don't crash the album.

## Lossy → lossy guard

`--format aac/mp3` against an MP3/OGG source falls back to ALAC for that track unless `--allow-lossy-recompress`. AUTO never triggers this since MP3 explicitly transcodes to AAC by design.

## MP3-in-MP4 deliberately avoided

When a source MP3 is processed under `--format auto`, the output is **transcoded to AAC** rather than stream-copied into an `.m4a` container. MP3-in-MP4 is a valid combination per the MP4 spec and `mutagen` reads its tags fine, but Finder / Music.app's metadata pipeline shortcuts based on the codec field and won't display tags reliably. Transcoding loses a tiny amount of audio fidelity (transparent on Bluetooth playback) in exchange for a tag schema every consumer can read.

## Cases the pipeline does NOT handle (deliberate)

### "Radio Show -" pseudo-artists

Some rips deliberately tag the `album_artist` as a *programme* rather than a *person* — Armin van Buuren's *A State Of Trance* weekly show ships with `album_artist = "Radio Show - A State Of Trance"` on every episode. We trust the tag; episodes land under `output/Radio Show - A State Of Trance/2025 - A State Of Trance 1254 (...)/` rather than mixed into `Armin van Buuren/`.

This is the right default — keeps weekly shows from polluting the main artist library — but means searching for the actual artist won't find them. Workarounds: re-tag source files, or future `--artist-override` flag.

### Classical-style compilations

`Best Of/` wrapper folder with one sub-folder per composer — each containing tracks tagged only with the composer name, no `ALBUM` tag — produces **one output album per composer-folder**, not a single merged "Various Artists" comp. Tried merging once; concluded per-composer artist folders were the better default.
