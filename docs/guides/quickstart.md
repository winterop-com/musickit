# Quickstart

Two minutes from zero to "music playing in the TUI". For the full end-to-end including iPhone + Tailscale + Amperfy, see the [Tutorial](tutorial.md).

## Install

```bash
uvx musickit --help                  # zero-install run; auto-fetches latest
uv tool install musickit             # for daily use (PATH-installed)
```

You'll also need `ffmpeg` + `ffprobe` for the convert pipeline:

```bash
brew install ffmpeg                  # macOS
sudo apt install ffmpeg              # Debian / Ubuntu
```

## Convert + audit + play

```bash
uvx musickit convert ./input ./output       # rip dirs → clean library
uvx musickit library audit ./output         # flag missing covers, gaps, etc.
uvx musickit tui ./output                   # browse + play locally
uvx musickit serve ./output                 # Subsonic API for clients
```

The convert step writes `output/<Artist>/<YYYY> - <Album>/NN - <Title>.m4a` at 256 kbps AAC by default; pass `--format alac` for archival lossless.

## Where to next

- **[Tutorial](tutorial.md)** — full walkthrough including iPhone + Tailscale + Amperfy.
- **[Architecture](../architecture.md)** — how the pieces fit together (audio subprocess, SQLite index, FFT visualizer).
- Per-command guides: [convert](convert.md) · [library](library.md) · [tui](tui.md) · [serve](serve.md) · [inspect](inspect.md).
- **[Development](development.md)** — for working on musickit itself (`git clone` + `uv sync`).
