# musickit

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
  - `library cover DIR IMAGE` / `library cover-pick DIR` / `library retag DIR` — in-place tag and cover edits; semi-automated cover selection via [musichoarders.xyz](https://covers.musichoarders.xyz/)
  - `library index status|drop|rebuild DIR` — manage the persistent SQLite index at `<DIR>/.musickit/index.db`
- **`musickit tui`** — Textual UI: artist/album browser, now-playing visualizer, internet radio, and a Subsonic-client mode that connects to your own `serve` over Tailscale.
- **`musickit serve`** — Subsonic-compatible HTTP server. Any Subsonic client (Symfonium, Amperfy, play:Sub, Feishin) can browse + stream + control via the standard API. mDNS / Bonjour for autodiscovery, ffmpeg-on-the-fly for transcoding, filesystem watcher for auto-rescan when you drop new albums in.
- **`musickit inspect`** — quick tag dump for a single file.

## Quickstart

```bash
uv sync
uv run musickit convert ./input ./output
```

That's it. See [Quickstart](guides/quickstart.md) for the full walkthrough, [Convert](guides/convert.md) for codec/bitrate options, [Serve](guides/serve.md) for the Subsonic API.

## Why this exists

Years of rip-collection wrangling produces an audio library full of:

- Scene-tag noise (`[FLAC]`, `[16Bit-44.1kHz]`, `[somesite.com]`)
- Multi-disc layouts in 6 different conventions (`CD1`/`CD2`, `Disc 1`/`Disc 2`, `Album (CD1)`/`Album (CD2)`, …)
- Tagless tracks that need filename parsing to recover artist / title
- Various-Artists rips with `album_artist = "VA"` and the real artist hiding in the filename
- Cover art that's either missing, low-resolution, or back-cover-by-mistake

`musickit convert` handles all of these; the rest of the CLI gives you tools to browse, play, audit, and stream the result.

## Status

Five top-level commands shipped (`convert`, `library`, `inspect`, `tui`, `serve`); `library` carries the read/mutate/manage subcommands (`tree`, `audit`, `fix`, `cover`, `cover-pick`, `retag`, `index`). mypy + pyright + ruff clean, full pytest suite green. Real-world tested against Symfonium / Amperfy / play:Sub / Feishin clients.

Roadmap items still open are listed at [Roadmap](roadmap.md).
