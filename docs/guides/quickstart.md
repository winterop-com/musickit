# Quickstart

This page gets you from zero to "music playing in a Subsonic client over Tailscale" in about 15 minutes.

## Install

```bash
git clone https://github.com/winterop-com/musickit
cd musickit
uv sync
```

`uv` will pull every Python dep including the bundled FFmpeg + PortAudio wheels for the TUI / serve audio paths. The convert pipeline itself needs `ffmpeg` and `ffprobe` on your `$PATH`:

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian / Ubuntu
```

That's it for setup.

## Convert your first library

```bash
uv run musickit convert ./input ./output
```

Default is `--format auto` — every track ends up `.m4a` with AAC inside, at 256 kbps. Apple Music quality, ~24% the size of lossless. See [Convert](convert.md) for the full codec / bitrate matrix.

Useful flags worth knowing on the first run:

```bash
uv run musickit convert ./input ./output --dry-run           # plan only, no writes
uv run musickit convert ./input ./output --verbose           # per-track log lines
uv run musickit convert ./input ./output --format alac       # archival lossless
uv run musickit convert ./input ./output --remove-source     # delete source after success
```

After it finishes you'll have `output/<Artist>/<YYYY> - <Album>/NN - <Title>.m4a` for every album.

## Audit + fix the result

```bash
uv run musickit library audit ./output
```

Flags every album with a problem (missing cover, mixed years, tag/path mismatch, track gaps, scene-residue artist names, etc.). Use `musickit library fix ./output` to apply the deterministic fixes (MusicBrainz year backfill, dir-rename to match tags). See [Library](library.md).

For low-res or missing cover art, the semi-automated fix is:

```bash
uv run musickit library cover-pick ./output
```

Walks the flagged albums one at a time, opens [musichoarders.xyz](https://covers.musichoarders.xyz/) pre-filled, you paste the chosen image URL back into the terminal, it downloads + saves + embeds.

## Browse + play locally

```bash
uv run musickit tui ./output
```

Three-pane TUI: sidebar with the artist→album browser, main area with the now-playing meta + 24-band visualizer + progress + tracklist, bottom keybar. Decoder + sounddevice output run in a separate process (their own GIL) so UI work can't stall playback into a buffer underrun. Both libraries ship pip wheels — no system audio install needed.

Press `?` for the full keybindings panel. The most useful: `Enter` to play, `Space` to pause, `n`/`p` for next/prev, `</>` for ±5s seek, `Ctrl+P` for the command palette, `q` to quit.

If you have an AirPlay device on the LAN, press `a` to route playback to it. See [TUI](tui.md) for the full feature set.

## Stream over your LAN / Tailscale

```bash
uv run musickit serve ./output
```

This starts a Subsonic-compatible HTTP server, binds `0.0.0.0:4533` by default (so Tailscale and your LAN both reach it), advertises itself via mDNS/Bonjour, and watches the library directory for changes. Default credentials are `admin` / `admin` with a yellow warning — pass `--user` and `--password` (or write `~/.config/musickit/serve.toml`) for anything beyond a private LAN.

Then on your phone, install **Symfonium** (Android), **Amperfy** (iOS), or **Feishin** (desktop), point it at the URL, and you're done. See [Serve](serve.md) for the full endpoint list, Tailscale walkthrough, and client recommendations.

## Where to next

- **More codec / format detail**: [Convert](convert.md)
- **Audit rules + fix loop**: [Library](library.md)
- **In-place tag edits**: [`library retag` / `library cover`](library.md#cover-embed-an-image)
- **TUI features incl. AirPlay + Subsonic-client mode**: [TUI](tui.md)
- **Subsonic API endpoints + client compat**: [Serve](serve.md)
- **Architecture + contributing**: [Development](development.md)
