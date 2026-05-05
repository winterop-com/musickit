# Architecture

How the pieces fit together — process model, data flow, every dependency that
sits behind a public command.

## The five user-facing surfaces

```
                   +----------+
                   | musickit |  CLI (typer)
                   +----+-----+
                        |
   +---------+----------+----------+----------+----------+
   |         |          |          |          |          |
convert    library    inspect      tui        serve
   |         |          |          |          |
ffmpeg    SQLite      mutagen   Textual    FastAPI
encode    + audit     dump      + audio    + watcher
                                subprocess + index cache
```

Every command lives in `src/musickit/cli/`. The `library` command is itself a
Typer **subapp** carrying further verbs (`tree`, `audit`, `fix`, `cover`,
`cover-pick`, `retag`, `index status|drop|rebuild`).

## End-to-end data flow

```
+--------------------+
|    input dir       |   raw rips: scene tags, mixed disc layouts,
|  (FLAC/MP3/M4A/    |   tagless tracks, missing covers
|   WAV/OGG/OPUS)    |
+----+---------------+
     |
     |  musickit convert
     v
+--------------------+
|  output dir        |   <Artist>/<YYYY> - <Album>/NN - <Title>.m4a
|  (clean library)   |   one shape, all M4A/AAC unless --format said otherwise
+----+---------------+
     |
     |  musickit library tree | audit | fix | cover-pick | retag
     |
     |  (also: hydrates `<output>/.musickit/index.db`,
     |   the persistent SQLite index of every album/track/
     |   audit-warning + multi-genre)
     v
+--------------------+         +-----------------------------------+
|  in-memory         |  same   |  <output>/.musickit/index.db      |
|  LibraryIndex      |<------->|  derived cache; rebuilt on schema |
|  (Pydantic graph)  |         |  bump or root mismatch            |
+----+---------------+         +-----------------------------------+
     |
     +--> consumed by `tui` and `serve` (both read the same index)
                              |
   +--------------------------+--------------------------+
   |                                                     |
   v                                                     v
+----------------------+                +---------------------------+
| musickit tui         |                | musickit serve            |
|                      |                |                           |
| Textual UI process   |                | FastAPI app               |
|   + audio engine     |                |   + IndexCache            |
|     subprocess       |                |   + LibraryWatcher        |
|     (PyAV decode +   |                |   + mDNS advertisement    |
|      sounddevice     |                |                           |
|      callback)       |                | Subsonic-compatible API   |
|   + AirPlay path     |                | over LAN / Tailscale      |
|     (pyatv)          |                |                           |
+----------------------+                +---------+-----------------+
                                                  |
                                                  v
                                       +--------------------+
                                       | Subsonic clients   |
                                       | Symfonium  / iOS   |
                                       | Amperfy    / iOS   |
                                       | play:Sub   / iOS   |
                                       | Feishin    / desk. |
                                       +--------------------+
```

## The convert pipeline (`src/musickit/pipeline/`)

Pure batch process — runs to completion and exits. No daemon, no IPC.

`pipeline.run.run_convert()` is the orchestrator. Per album:

1. **`discover.py`** — walks the input tree, groups files by leaf directory,
   merges multi-disc layouts (`Album/CD1/`, `Album/Disc 1/`, etc.) into one
   logical album.
2. **`metadata.read.read_source()`** — uses mutagen to pull tags + embedded
   picture from one representative track per album. Lowercase-stripped values
   get `smart_title_case` so `the beatles` becomes `The Beatles` while real
   casing (`AC/DC`, `iPhone`, `R.E.M.`) is preserved.
3. **`enrich/`** — optional MusicBrainz / Cover Art Archive / AcoustID
   lookups when tags are missing or the cover is low-res. AcoustID uses
   chromaprint fingerprints (via fpcalc) to identify tagless tracks by audio
   content alone.
4. **`pipeline.cover`** — pick the best cover from candidates: embedded ≥
   sidecar (`cover.jpg`/`folder.jpg`) ≥ MB CAA ≥ none. Normalise via Pillow
   (max-edge resize, JPEG quality 90).
5. **`convert.py`** — call `ffmpeg` to encode each track to the target format.
   Default is 256 kbps AAC m4a (Apple Music quality, ~24% the size of
   lossless). `--format alac` for archival lossless; `--format passthrough`
   for remux only.
6. **`pipeline.album.write_album()`** — write the encoded files into
   `<output>/<Artist>/<YYYY> - <Album>/NN - <Title>.m4a`, with mutagen
   writing tags + embedded cover.

`pipeline.dedupe` skips albums whose hash already exists in the output (cheap
restart safety). `pipeline.report` accumulates per-album outcomes for the
final summary table.

## The library index (`src/musickit/library/`)

The same Pydantic `LibraryIndex` graph (Artist → Album → Track) is consumed
by **every** command that reads a converted library — `library tree/audit/fix`,
`tui`, `serve`. It's defined once in `library/models.py` and built two ways:

### From the filesystem (`library/scan.py`)

`scan(root)` walks `root` with `Path.rglob`, groups audio files by parent
directory (= album), reads tags with mutagen, and returns a fresh
`LibraryIndex`. This is the cold path — used the very first time a library is
seen, and any time the user passes `--no-cache`.

`audit(index)` (`library/audit.py`) attaches `warnings` to each album by
running rules: no cover, low-res cover, missing/mixed years, mixed
album_artist, scene-residue dirnames, tag/path mismatch, track gaps. Pure
analysis — no I/O after the scan.

`fix_index(index, ...)` (`library/fix.py`) acts on the warnings: MusicBrainz
year backfill (one HTTP call per flagged album), tag/path mismatch resolution
(rename dir to match tag, or invert with `--prefer-dirname`), `--rename` after
`retag`. Each fix mutates the in-memory model AND the on-disk file/dir.

### From the SQLite cache (`library/db.py`, `library/load.py`)

`<root>/.musickit/index.db` persists the `LibraryIndex` so cold starts
(launching `tui` or `serve`) don't re-read every audio tag. Tables:

| Table | Holds |
|---|---|
| `meta` | `schema_version`, `library_root_abs`, `last_full_scan_at` |
| `albums` | One row per album dir — tags, counts, `dir_mtime`, audit-relevant flags |
| `tracks` | One row per audio file — tags, ReplayGain, `file_mtime`, `file_size` |
| `track_genres` | `(track_id, genre)` pairs for multi-genre support |
| `album_warnings` | `(album_id, warning)` pairs from the audit pass |

`load_or_scan(root)` is the top-level entry point used by `tui`, `serve`,
and the library subcommands:

```
load_or_scan(root):
  conn = open_db(root)            # creates schema if missing; unlinks +
                                  # rebuilds on schema_version mismatch
                                  # or library_root_abs mismatch
  if is_empty(conn):
      scan_full(root, conn)       # full FS walk, audit, write all rows
  else:
      load(root, conn)            # hydrate Pydantic from rows
      validate(root, conn)        # diff FS vs DB; per-album re-scan for
                                  # added / removed / tag-edited dirs
```

`validate()` uses `(file_mtime, file_size)` per track row to detect changes
without re-reading every tag. Affected album dirs go through `rescan_albums`,
which deletes the old album row (cascade-drops tracks + warnings) and inserts
a fresh one. Whole-album deletions are detected when the DB has a row for a
dir that no longer exists on disk.

The `serve` watcher (`serve/watcher.py`) drives the same `rescan_albums`
through `IndexCache.rescan_paths(paths)` whenever filesystem events fire
during the debounce window.

Schema bumps don't run migrations — `db.py` defines a `SCHEMA_VERSION`
constant; mismatched DBs are unlinked and rebuilt from scratch. The
filesystem is the source of truth so destructive rebuilds are always safe.

## The TUI process model (`src/musickit/tui/`)

Two processes:

```
+-----------------------------------+        +-------------------------------+
| UI process (Textual)              |        | Audio engine subprocess       |
| - MusickitApp                     |        | - engine_main()               |
| - render loop                     |        | - opener thread (per play)    |
| - keybinding dispatch             |  <-->  | - decoder thread (PyAV)       |
| - AudioPlayer (RPC client)        |        | - sounddevice OutputStream    |
| - AirPlayController (pyatv)       |        |   callback                    |
+-----------------------------------+        +-------------------------------+
              ^                ^                            ^         ^
              |                |                            |         |
              | reads          | sends Commands             | reads   | writes
              | shared mem     | (PLAY / PAUSE / SEEK /     | shared  | shared
              | (position,     |  SET_VOLUME / STOP / ...)  | mem     | mem
              |  band_levels)  |                            |         |
              |                v                            |         v
              |         +-----------------+                 |  +-----------------+
              |         | cmd_queue       |  ----------->   |  | event_queue     |
              |         | (mp.Queue)      |                 |  | (mp.Queue)      |
              |         +-----------------+                 |  +-----------------+
              |                                             |  TRACK_END /
              |                                             |  TRACK_FAILED /
              |                                             |  METADATA_CHANGED
              |                                             |  / STARTED
              |                                             |  drained by
              |                                             |  reader thread
              |                                             |  in UI process
              v                                             v
        +---------------------+                       +---------------------+
        | Shared memory       |                       | (events fire UI    |
        | mp.Value:           |                       |  callbacks via     |
        |   position_frames   |                       |  Textual's         |
        |   duration_s        |                       |  call_from_thread) |
        |   paused / stopped  |                       +---------------------+
        |   volume            |
        |   replaygain_mult.  |
        | mp.Array:
        |   band_levels[48]   |
        +---------------------+
```

### Why a subprocess?

The sounddevice audio callback is implemented in Python — it acquires the
GIL on every fire (~43 Hz at 1024 frames @ 44.1 kHz). When the audio engine
shared a Python interpreter with the Textual UI, a burst of UI work
(window resize, focus switch between panes, GC) could hold the GIL long
enough to starve the callback past PortAudio's deadline → buffer underrun
→ audible click (xrun).

Earlier mitigations stacked workarounds: 500 ms PortAudio buffer, then
1 s buffer, resize-debounce, focus-change short-circuits. The current
architecture eliminates the root cause: the audio engine has its own
interpreter, its own GIL. UI work in the main process can't stall the
callback. The PortAudio buffer is back to 200 ms.

### Public interface stays the same

`AudioPlayer` is the public class the UI imports. After the subprocess
move, its methods (`play`, `stop`, `toggle_pause`, `seek`, `set_volume`,
`set_airplay`, `set_replaygain_mode`) are thin RPC wrappers — each pushes
a `Command` onto `cmd_queue` (or writes shared memory). Properties
(`position`, `band_levels`, `is_playing`, `volume`) read shared memory
directly. The UI code in `app.py` doesn't know there's a subprocess.

A reader thread in the UI process drains `event_queue` and dispatches to
the registered callbacks (`on_track_end`, `on_track_failed`,
`on_metadata_change`). The callbacks fire from the reader thread; UI
updates inside them route through Textual's `call_from_thread` (same as
they did in the previous threaded design).

### AirPlay stays in the UI process

`pyatv` is asyncio-based and just sends URLs to the AirPlay device; it
doesn't run a decoder. There's no audio callback to protect. So the
`AirPlayController` lives alongside the UI and is consulted directly by
`AudioPlayer.play()` / `set_volume()` / `toggle_pause()` when a device is
connected.

## The visualizer (FFT path)

The visualizer is a 48-band spectrum analyser. The math is identical in
every audio player that has ever drawn one — only the UI plumbing is
musickit-specific.

### What the audio callback hands off

Every callback (~43 Hz) gets a chunk of N stereo float32 samples from the
decoder queue and writes it to PortAudio's output buffer. As a side-effect
it stashes a reference to that chunk on the engine instance, then runs
the FFT before publishing the resulting band magnitudes into shared
memory. Roughly:

```python
# inside AudioEngine._audio_callback (audio thread, runs in subprocess)
outdata[:] = chunk * volume * replaygain_multiplier   # the actual playback
self._latest_chunk = chunk                            # for the visualizer
self._update_band_levels()                            # FFT + decay (below)
self._publish_band_levels()                           # write 48 floats to mp.Array
```

The UI ticks at 30 Hz, reads the 48 floats from shared memory, and renders
the bars. Producer-faster-than-consumer is fine: the UI just sees the most
recent published values.

### Step 1: turn samples into a frequency spectrum

Audio samples are amplitude over time — a wiggle. The FFT (Fast Fourier
Transform) decomposes that wiggle into the strengths of every constituent
sinewave. NumPy does it in one call:

```python
mono = chunk.mean(axis=1)            # collapse stereo to mono
spectrum = np.abs(np.fft.rfft(mono)) # rfft = real-input FFT (twice as fast as
                                     # the complex version since audio is real)
                                     # abs() turns complex amplitudes into
                                     # magnitudes — "how loud is this frequency".
```

For a 1024-sample chunk at 44.1 kHz, `rfft` returns 513 magnitude bins
covering 0 Hz → 22 050 Hz (the Nyquist limit). Each bin is ~43 Hz wide
(`samplerate / chunk_size`).

### Step 2: group bins into bands the eye can read

513 bars don't fit on screen and most of them carry no useful info — the
top 100 bins are usually inaudible content above 18 kHz. Two more
problems with showing raw bins:

- Linear bin spacing is wrong for music. Humans perceive pitch
  geometrically: an octave is a doubling of frequency. C4 → C5 is 261 Hz
  → 523 Hz; C5 → C6 is 523 → 1046 Hz. Equal-width linear bands collapse
  the bass into 1-2 bars and waste 30 bars on the brilliance range.
- Energy is concentrated in the bass. Without geometric spacing, almost
  every musical signal renders as "leftmost bar maxed out, rest twitching".

Solution: pick band edges on a logarithmic scale. `np.geomspace(1, n_bins, 49)`
returns 49 numbers between 1 and `n_bins` that double-ish from one to the
next. Band `i` covers bins `edges[i] .. edges[i+1]`, and we take the
loudest bin in that range:

```python
edges = np.geomspace(1, n_bins, VIS_BANDS + 1).astype(int)
for i in range(VIS_BANDS):
    lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
    peak = float(spectrum[lo:hi].max())   # loudest bin in this geometric band
```

`max` (peak) instead of `mean` makes percussive transients (a snare, a
hi-hat) pop visually — averaging would smear them across neighbouring bins.

### Step 3: normalise + smooth

Raw peak magnitudes have no fixed scale (they depend on the chunk size and
input gain). The `/ 32.0` is a magic number tuned by ear so a typical
loud passage hits 0.7-0.9 and rare peaks reach 1.0. The `min(1.0, ...)`
clamps so a clipped input doesn't push bars off-screen.

```python
level = min(1.0, peak / 32.0)
prev = self._band_levels[i]
self._band_levels[i] = max(level, prev * _VIS_DECAY)
```

The `max(new, prev * decay)` is exponential smoothing on the way DOWN
only. New peaks pop instantly; bars then decay by 15% per callback
(`_VIS_DECAY = 0.85`), so a momentary peak fades over ~0.5 s instead of
disappearing in 23 ms. Asymmetric attack/release is what makes the bars
look "physical" rather than flickering with the sample-rate noise floor.

### Step 4: render columns of unicode blocks

The Visualizer widget in `tui/widgets.py` consumes the 48 floats and
draws a column of full blocks (`█`) per band, with sub-cell vertical
resolution from the partial-block characters `▁▂▃▄▅▆▇█` (the top of each
bar). Bar width is computed to fill the available content area: prefer
1-cell gaps between bars; drop the gap on terminals where even
`bar_width=1` with gaps wouldn't fit. Leftover modulo cells become a
leading offset so the meter is centered.

## The serve process (`src/musickit/serve/`)

Single FastAPI process. Components:

```
+-------------------------------------------------+
|  uvicorn                                        |
|    +------------------------------------------+ |
|    | FastAPI app (serve/app.py)               | |
|    |  - Subsonic auth dependency              | |
|    |  - PostFormToQueryMiddleware             | |
|    |    (play:Sub iOS sends creds in form)    | |
|    |  - SubsonicFormatMiddleware              | |
|    |    (XML default; ?f=json opts in)        | |
|    |  - 7 endpoint routers under /rest/       | |
|    +------------------------------------------+ |
|                                                 |
|    app.state.cache = IndexCache(root)           |
|      - LibraryIndex                             |
|      - albums_by_id / tracks_by_id /            |
|        artists_by_id (Subsonic-ID lookups)      |
|      - rebuild() / rescan_paths()               |
|                                                 |
|    app.state.watcher = LibraryWatcher(cache)    |
|      - watchdog Observer                        |
|      - debounce timer (5s)                      |
|      - dispatches changed paths to              |
|        cache.rescan_paths(...)                  |
|                                                 |
|    mDNS service (Zeroconf) advertises           |
|    `_subsonic._tcp.local` so clients on the     |
|    LAN find the server without typing its IP    |
+-------------------------------------------------+
```

The `IndexCache` is the same pattern as the TUI's `AudioPlayer` cache:
public methods do small things on top of the shared `LibraryIndex` /
`load_or_scan` machinery. Endpoints in `serve/endpoints/` resolve
incoming Subsonic IDs against `albums_by_id` / `tracks_by_id` /
`artists_by_id` (built from the index by `_reindex`); none of them hit
the disk on a hot request — even `getCoverArt` has the path resolved
from the cache and only opens the on-disk image bytes for the response.

The `/rest/stream` endpoint either streams raw bytes (when no transcode
is requested) or pipes through ffmpeg-on-the-fly when the client asks for
`format=mp3` or `maxBitRate=N`. Symfonium / Amperfy / play:Sub clients
auto-negotiate this via `getMusicFolders` / `getOpenSubsonicExtensions`
on first connect.

### Subsonic-ID stability

`serve/ids.py` builds opaque Subsonic IDs from each entity's identity:

```python
def artist_id(artist_dir: str) -> str:
    return "ar_" + hashlib.sha1(artist_dir.encode("utf-8")).hexdigest()[:16]

def album_id(album: LibraryAlbum) -> str:
    return "al_" + hashlib.sha1(str(album.path).encode("utf-8")).hexdigest()[:16]

def track_id(track: LibraryTrack) -> str:
    return "tr_" + hashlib.sha1(str(track.path).encode("utf-8")).hexdigest()[:16]
```

The 2-char prefixes (`ar_` / `al_` / `tr_`) prevent cross-type collisions
and make IDs visually classifiable in logs / debugging. Hashing path
strings means IDs are stable across rescans for unchanged entities
(rebuild recomputes from the same input) and across server restarts.
Symfonium / Amperfy / play:Sub cache IDs per-server, so any churn would
force re-downloads on every client. Reverse lookup is O(1) via the
`albums_by_id` / `tracks_by_id` / `artists_by_id` dicts on
`IndexCache`, populated by `_reindex`.

If the user renames an album dir or moves a file, the hash changes and
clients see it as a new entity. That's the right behavior — moved
content really is logically different — but it's worth knowing if you
plan a directory reorganisation, since clients will lose any
"recently played" / "starred" state tied to the old IDs.

### IndexCache rebuild atomicity

`IndexCache._reindex` does NOT take a lock around its writes — it
mutates `self.index`, `self.albums_by_id`, `self.tracks_by_id`,
`self.artists_by_id`, `self.artist_name_by_id` in sequence. The
guarantee callers rely on is **per-attribute atomicity** from CPython's
GIL: an endpoint reading `self.albums_by_id[album_id]` either sees the
old dict reference or the new one, never a half-mutated dict. It can,
however, see a mix between old and new across attributes (a brand-new
album in `albums_by_id` paired with the old `index.albums` list)
during the ~microsecond window of `_reindex`.

In practice this is fine because endpoints touch one or two attributes
each and inconsistencies resolve on the next request. If we ever needed
strict cross-attribute snapshot reads, we'd build a single replacement
state object outside the lock and assign it via one rebind — but the
current pattern is simpler and the inconsistency window is too short to
ever observe in real Subsonic-client traffic.

## The watcher (`src/musickit/serve/watcher.py`)

`watchdog` `Observer` watches the library root recursively. The handler
filters by extension (audio files only — skip `.DS_Store`, `cover.jpg`)
and pushes paths into a set during a debounce window (default 5 s). When
no new event has arrived for the debounce period, the whole batch goes
to `cache.rescan_paths(paths)`, which:

1. Resolves each path to its album dir (file → parent; existing dir →
   self; vanished → both, since we can't tell file-vs-dir from a missing
   path).
2. Calls `library.rescan_albums` to delete + re-insert only those albums
   in one transaction.
3. Refreshes the in-memory `LibraryIndex` and the reverse-lookup dicts
   from the new rows.

Dropping a brand-new album into the library directory therefore takes ~6 s
to appear (5 s debounce + scan + rebuild dicts). A bulk copy of 100 files
collapses to one rescan.

### Client-triggered rescan via the Subsonic API

Subsonic clients (Symfonium / Amperfy / Feishin) usually have a "refresh
library" button that hits two standard endpoints:

- `GET /rest/startScan` — kicks off a non-blocking rescan and returns
  immediately with `{scanning: true, count: <current-track-count>}`.
  musickit backs this with `cache.start_background_rescan(force=True)`,
  which spawns a daemon thread running the same full rebuild as
  `musickit library index rebuild`.
- `GET /rest/getScanStatus` — poll endpoint, returns `{scanning: bool,
  count: <track-count>}`. The client polls every second or two while
  `scanning=true`, then refreshes its in-app library view.

Streaming endpoints (`/rest/stream`, `/rest/getCoverArt`) are
completely independent of the rescan path: stream reads bytes
straight off disk via PyAV/ffmpeg without touching the index. The
rescan thread does mutate the in-memory dicts (`albums_by_id` etc.)
but `IndexCache._reindex` uses an atomic per-attribute swap, so a
concurrent `/rest/getAlbum?id=X` sees either the old dict or the
new dict, never a partial state. **A track playing on Amperfy
doesn't skip during a server-side rescan.**

The watcher's auto-rescan covers most workflows, so an explicit
`startScan` from the client is usually unnecessary — drop a file in,
wait 5 seconds, pull-to-refresh the client.

## Where every dependency sits

| Package | Used by | Purpose |
|---|---|---|
| `typer` | `cli/` | CLI plumbing (subcommands, options, autocompletion). |
| `mutagen` | `metadata/`, `pipeline/` | Read/write tags for FLAC, MP3, MP4, WAV, OGG, OPUS. |
| `Pillow` | `cover.py`, `pipeline/` | Decode + resize cover images. Defensive against malformed JPEGs. |
| `httpx` | `enrich/`, `tui/subsonic_client.py` | MusicBrainz / Cover Art Archive / AcoustID / Subsonic API HTTP. |
| `pydantic` | `library/`, `metadata/` | Data models with type-checking and round-trippable JSON. |
| `rich` | `cli/`, `library/` | Terminal tables, trees, progress bars. |
| `textual` | `tui/` | The TUI itself (widgets, layout, async event loop). |
| `PyAV` | `tui/audio_engine.py` | Audio decoding (FLAC, MP3, M4A, OGG, OPUS, Icecast streams). Wraps FFmpeg. |
| `sounddevice` | `tui/audio_engine.py` | Cross-platform audio output via PortAudio. |
| `numpy` | `tui/audio_engine.py` | FFT + array math for the visualizer. |
| `pyatv` | `tui/airplay.py`, `tui/airplay_picker.py` | AirPlay device discovery + control. |
| `zeroconf` | `serve/discovery.py`, `tui/discovery.py` | mDNS/Bonjour: server advertises, TUI auto-detects. |
| `watchdog` | `serve/watcher.py` | Filesystem-event observer for auto-rescan. |
| `FastAPI` | `serve/` | HTTP framework; endpoint routing, JSON serialization. |
| `uvicorn` | `cli/serve.py` | ASGI server that runs the FastAPI app. |

External binaries: `ffmpeg` and `ffprobe` for the convert pipeline +
on-the-fly transcoding. Optional: `chromaprint` (`fpcalc`) for AcoustID.

## Where to read next

- [Convert](guides/convert.md) — pipeline stages in detail.
- [Library](guides/library.md) — audit rules + the SQLite index.
- [TUI](guides/tui.md) — keybindings + AirPlay + Subsonic-client mode.
- [Serve](guides/serve.md) — Subsonic API + Tailscale + client setup.
- [Quickstart](guides/quickstart.md) — end-to-end walkthrough including iPhone streaming.
- [Development](guides/development.md) — directory layout + test patterns.
