# musickit

Python 3.13 CLI for converting audio rips into a clean tagged library, browsing and playing it via a Textual TUI, and streaming it over Tailscale via a Subsonic-compatible HTTP server.

## Quickstart

```bash
uv sync
uv run musickit convert ./input ./output       # convert
uv run musickit library ./output --audit       # audit
uv run musickit tui ./output                   # TUI
uv run musickit serve ./output                 # Subsonic server
```

## Documentation

Full docs are at **[docs/](docs/index.md)** — built with MkDocs Material. Run them locally:

```bash
make docs-serve     # http://127.0.0.1:8000
```

Or jump straight to:

- [Quickstart](docs/guides/quickstart.md) — install + first convert
- [musickit convert](docs/guides/convert.md) — codec / bitrate / enrichment matrix
- [musickit library](docs/guides/library.md) — audit rules + auto-fix
- [musickit tui](docs/guides/tui.md) — TUI: local + radio + Subsonic-client + AirPlay
- [musickit serve](docs/guides/serve.md) — Subsonic API + Tailscale + clients
- [Edge cases](docs/edge-cases.md) — every weirdness encountered on real rips
- [Roadmap](docs/roadmap.md) — what's next
- [Development](docs/guides/development.md) — architecture + contributing

## Status

v0.3.0 · 297 tests, ruff + mypy + pyright clean. All eight user-facing commands shipped: `convert`, `inspect`, `library`, `retag`, `cover`, `cover-pick`, `tui`, `serve`. The TUI ships local-library playback, internet radio, Subsonic-client mode, AirPlay output (incl. pause + volume routing), mDNS discovery, ReplayGain normalisation, an incremental `/`-filter, in-place tag editing (`e` for track / album-wide), and a 24-band FFT visualiser. The server is OpenSubsonic-compatible (`multipleGenres`, `transcodeOffset`, `songLyrics` extensions) and tested against Symfonium / Amperfy / play:Sub / Feishin clients on iOS / Android / desktop.

## License

See LICENSE in the repo root.
