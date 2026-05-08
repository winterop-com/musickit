# MusicKit

A Python 3.13 CLI that converts arbitrary audio rips (FLAC / MP3 / M4A / WAV / OGG / OPUS) into a clean, tagged, organised library — then lets you browse and play it locally via a Textual TUI or stream it over your LAN / Tailscale via a Subsonic-compatible HTTP server.

## What it does

```
input/                            output/
└── messy rips/                   └── Artist/
    [FLAC] Some Album (CD1)/          └── 2012 - Album Name/
       01-track.flac      ─►              ├── 01 - First Track.m4a
       ...                                ├── 02 - Second Track.m4a
                                          └── cover.jpg
```

End-to-end pipeline:

1. **Walk** the input tree, group by leaf directory, merge multi-disc layouts.
2. **Read** source tags (mutagen for FLAC / MP3 / MP4) plus filename fallback for tagless rips.
3. **Re-encode** via `ffmpeg`, default to 256k AAC m4a (Apple Music quality, ~24% the size of lossless).
4. **Pick a cover** — embedded, sidecar, or online via MusicBrainz + Cover Art Archive.
5. **Write clean tags** + the normalised cover; lay out as `output/<Artist>/<YYYY> - <Album>/NN - <Title>.m4a`.

Then on top of that:

- **`musickit library`** — read, audit, fix, retag, cover, and manage the converted library. Subcommands:
  - `library tree DIR` / `library audit DIR` / `library fix DIR` — render, audit, auto-fix
  - `library cover IMAGE DIR` / `library cover-pick DIR` / `library retag DIR` — in-place tag and cover edits; semi-automated cover selection via [musichoarders.xyz](https://covers.musichoarders.xyz/)
  - `library lyrics fetch DIR` — populate `<track>.lrc` sidecars from [LRCLIB](https://lrclib.net) (free, no API key, returns synced lyrics for popular tracks)
  - `library index status|drop|rebuild DIR` — manage the persistent SQLite index at `<DIR>/.musickit/index.db`
- **`musickit tui`** — Textual UI: artist/album browser, now-playing visualizer, internet radio, saved Mixes view, and a Subsonic-client mode that connects to your own `serve` over Tailscale. Press `g` on any track to generate a 60-min mix anchored to it; press `l` to swap the visualizer for synced lyrics that track playback line-by-line.
- **`musickit serve`** — Subsonic-compatible HTTP server. Any Subsonic client (Symfonium, Amperfy, play:Sub, Feishin) can browse + stream + control via the standard API. mDNS / Bonjour for autodiscovery, ffmpeg-on-the-fly for transcoding, filesystem watcher for auto-rescan when you drop new albums in. Real heart / star button (persistent favourites at `<root>/.musickit/stars.toml`); LRC bodies promoted to `synced: true` so client lyrics views highlight live.
- **`musickit playlist`** — auto-generate `.m3u8` playlists anchored to a seed track using tag-based similarity (artist / genre / year). `gen` writes a mix; `list` / `show` browse what's saved. Output is plain extended M3U so VLC, Subsonic clients, and the TUI's Mixes view all play it.
- **`musickit inspect`** — quick tag dump for a single file.
- **Desktop apps** — Tauri (~15 MB, native WebKit on macOS) and Electron (~120 MB, bundled Chromium) wrappers around a generic Subsonic client UI. URL + Username + Password login; salted-token auth; refresh-restores via URL hash. `.dmg` / `.exe` / `.AppImage` / `.deb` attach to every release. See [Desktop apps](guides/desktop.md).
- **Mobile** — no MusicKit app of its own; `serve` exposes the standard Subsonic API so play:Sub / Amperfy (iOS) and Symfonium / DSub / Tempo (Android) all work against it. See [Mobile](guides/mobile.md).

## Quickstart

```bash
uvx musickit convert ./input ./output
```

`uvx` downloads the latest `musickit` from PyPI, caches it, runs it. For persistent install: `uv tool install musickit`. New here?

- **[Quickstart](guides/quickstart.md)** — full end-to-end walkthrough including iPhone + Tailscale + Amperfy. ~30 minutes.
- **[Architecture](architecture.md)** — how the pieces fit together: process model, data flow, audio engine subprocess, SQLite index, FFT visualizer. Read this first if you want a mental model before diving in.

Per-command guides: [Convert](guides/convert.md) · [Library](guides/library.md) · [TUI](guides/tui.md) · [Serve](guides/serve.md) · [Playlist](guides/playlist.md) · [Inspect](guides/inspect.md).

Clients: [Desktop apps](guides/desktop.md) · [Mobile (Subsonic)](guides/mobile.md).

## Why this exists

Years of rip-collection wrangling produces an audio library full of:

- Scene-tag noise (`[FLAC]`, `[16Bit-44.1kHz]`, `[somesite.com]`)
- Multi-disc layouts in 6 different conventions (`CD1`/`CD2`, `Disc 1`/`Disc 2`, `Album (CD1)`/`Album (CD2)`, …)
- Tagless tracks that need filename parsing to recover artist / title
- Various-Artists rips with `album_artist = "VA"` and the real artist hiding in the filename
- Cover art that's either missing, low-resolution, or back-cover-by-mistake

`musickit convert` handles all of these; the rest of the CLI gives you tools to browse, play, audit, and stream the result.

## Status

Six top-level commands shipped (`convert`, `library`, `inspect`, `tui`, `serve`, `playlist`); `library` carries the read/mutate/manage subcommands (`tree`, `audit`, `fix`, `cover`, `cover-pick`, `retag`, `lyrics`, `index`); `playlist` carries `gen` / `list` / `show`. mypy + pyright + ruff clean, full pytest suite green. Real-world tested against Symfonium / Amperfy / play:Sub / Feishin clients with persistent favourites and synced lyrics.

Roadmap items still open are listed at [Roadmap](roadmap.md).
