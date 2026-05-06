# musickit

[![CI](https://github.com/winterop-com/musickit/actions/workflows/ci.yml/badge.svg)](https://github.com/winterop-com/musickit/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/musickit)](https://pypi.org/project/musickit/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://winterop-com.github.io/musickit/)

Python 3.13 CLI for converting audio rips into a clean tagged library, browsing and playing it via a Textual TUI, and streaming it over Tailscale via a Subsonic-compatible HTTP server.

## Install

The lowest-friction way is [`uvx`](https://docs.astral.sh/uv/) — it downloads, caches, and runs the latest published `musickit` in one step. No install step required:

```bash
uvx musickit --help
```

For daily / persistent use (PATH-installed, no per-run network check):

```bash
uv tool install musickit
musickit --help
```

You'll also need `ffmpeg` and `ffprobe` on `$PATH` for the convert pipeline:

```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian / Ubuntu
```

## Quickstart

```bash
uvx musickit convert ./input ./output                          # convert
uvx musickit library audit ./output                            # audit
uvx musickit tui ./output                                      # TUI
uvx musickit serve ./output                                    # Subsonic server
uvx musickit playlist gen ./output --seed <track> --minutes 60 # auto-generate a mix
```

## Screenshots

The TUI: artist browser on the left, drilled into an album, 48-band visualizer at the top.

![Drilled-in album view](docs/screenshots/album-tracks.svg)

Fullscreen visualizer (`f`):

![Fullscreen visualizer](docs/screenshots/fullscreen-viz.svg)

More screenshots in the [TUI guide](https://winterop-com.github.io/musickit/guides/tui/).

## Documentation

Full docs are at **[docs/](docs/index.md)** — built with MkDocs Material. Run them locally:

```bash
make docs-serve     # http://127.0.0.1:8000
```

Or jump straight to:

- [Architecture](docs/architecture.md) — how all the pieces fit together (process model, data flow, audio subprocess, SQLite index, FFT visualizer)
- [Quickstart](docs/guides/quickstart.md) — end-to-end walkthrough including iPhone + Tailscale + Amperfy
- [musickit convert](docs/guides/convert.md) — codec / bitrate / enrichment matrix
- [musickit library](docs/guides/library.md) — audit rules + auto-fix + SQLite index
- [musickit tui](docs/guides/tui.md) — TUI: local + radio + Subsonic-client + AirPlay
- [musickit serve](docs/guides/serve.md) — Subsonic API + Tailscale + clients
- [musickit playlist](docs/guides/playlist.md) — auto-generated `.m3u8` mixes anchored to a seed track
- [Edge cases](docs/edge-cases.md) — every weirdness encountered on real rips
- [Roadmap](docs/roadmap.md) — what's next
- [Development](docs/guides/development.md) — directory layout + test patterns + commit style

## Status

v0.9.2 · ruff + mypy + pyright clean, full pytest suite green. Six top-level commands — `convert`, `library`, `inspect`, `tui`, `serve`, `playlist` — with `library` carrying the read / mutate / manage subcommands (`tree`, `audit`, `fix`, `cover`, `cover-pick`, `retag`, `lyrics`, `index`) and `playlist` carrying `gen` / `list` / `show`. The TUI ships local-library playback, internet radio, Subsonic-client mode (with persistent token-auth credentials), AirPlay output (incl. pause + volume routing), mDNS discovery, ReplayGain normalisation, a diacritic-folded `/`-filter, in-place tag editing (`e` for track / album-wide), auto-generated Mixes (`g` to create, browseable view of saved `.m3u8`), a 48-band FFT visualiser with `f` / `v` toggles, synced lyrics via `l` (parsed `[mm:ss.xx]` markers, active line bolded as playback advances), and click-to-seek on the progress bar. Audio decoder + sounddevice callback run in a separate process so UI work in the main interpreter can't stall playback. The server is OpenSubsonic-compatible (`multipleGenres`, `transcodeOffset`, `songLyrics` extensions), backs heart / star buttons with a persistent `<root>/.musickit/stars.toml`, returns sub-ms FTS5-ranked `/search3` results, promotes LRC lyrics to `synced: true`, and is tested against Symfonium / Amperfy / play:Sub / Feishin clients on iOS / Android / desktop. A persistent SQLite library index at `<root>/.musickit/index.db` makes cold starts skip the filesystem walk + tag read; the filesystem watcher does per-album incremental rescans.

## License

See LICENSE in the repo root.
