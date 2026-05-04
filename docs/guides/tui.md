# `musickit tui`

A Textual TUI for browsing + playing the converted library, plus a curated radio section, plus a Subsonic-client mode for connecting to a remote `musickit serve` (or any Subsonic server) over Tailscale.

## Modes

```bash
musickit tui ./output                                # local library + radio
musickit tui                                         # radio-only
musickit tui --subsonic URL --user U --password P    # Subsonic client mode
musickit tui --discover                              # list LAN Subsonic servers + AirPlay devices, exit
musickit tui --airplay 'HomePod'                     # route playback to an AirPlay device
```

Subsonic credentials are NEVER persisted — pass `--subsonic` / `--user` / `--password` explicitly each session. With no arguments the TUI drops directly into radio-only mode.

## Layout

```
┌─ musickit ──────────────────────────────────────────────────────────────────┐
│  ♪ Duran Duran - Is There Something I Should Know · NTWICM                  │
│  00:06 / 04:08                                                  ▶ Playing   │
│  ▆▆▆▅▅▄▃▃                          VOL ███████████░░░  70%                  │
├─────────────────┬───────────────────────────────────────────────────────────┤
│ Library         │ ── Playlist ── [Shuffle] [Repeat: Off] [2/30] ──          │
│ ▸ Imagine Drag. │ ── Now That's What I Call Music (1983) ──                 │
│ ▸ Linkin Park   │  1.  Phil Collins - You Can't Hurry Love                  │
│ ▸ Robyn         │ ▶ 2. Duran Duran - Is There Something I Should Know       │
│ ▾ Various Art.  │  3.  UB40 - Red Red Wine                                  │
│   ├ 1983 NTWI…  │  4.  Limahl - Only For Love                               │
│   ├ 1984 NTWI…  │  ...                                                      │
│   └ ...         │                                                           │
├─────────────────┴───────────────────────────────────────────────────────────┤
│ ↕ Scroll  Enter Play  Spc ▶▍  ←→ Seek  Tab Focus  q Quit                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Top**: now-playing meta, 24-band FFT visualizer (FFT runs on the UI thread, not the audio callback), progress + state badge + volume.
- **Sidebar**: library stats + Artist→Album browser tree.
- **Main**: track list with `▶` marker on the playing row.
- **Bottom**: status bar + keybar.

## Keybindings

| Key | Action |
|---|---|
| `↑` / `↓` / `j` / `k` | Navigate within the focused pane |
| `Enter` | Play selected track / drill into selected album / connect to selected radio station |
| `Space` | Play / pause |
| `n` / `p` | Next / previous track |
| `<` / `>` | Seek -5s / +5s |
| `+` / `-` | Volume up / down |
| `s` | Toggle shuffle |
| `r` | Cycle repeat (off → album → track) |
| `f` | Toggle fullscreen visualizer |
| `/` | Filter the focused pane (artists / albums / tracks) |
| `e` | Edit tags — track-level on track list, album-wide on album row |
| `Tab` | Cycle focus across browser / track list |
| `Backspace` | Browser: go up one level |
| `Ctrl+R` / `F5` | Rescan library |
| `?` | Toggle full-keybindings help panel |
| `Ctrl+P` | Command palette (also surfaces playback verbs) |
| `a` | AirPlay device picker |
| `q` / `Ctrl+C` | Quit |

Click semantics on the track list mirror Spotify / iTunes: single click moves the cursor only (no playback), double click within ~400ms plays the track.

## Local library mode

`musickit tui ./output` walks the directory via `library.scan` + `library.audit`, builds an in-memory `LibraryIndex`, and renders. Initial scan shows a centred progress overlay with album-by-album feedback; subsequent rescans (`Ctrl+R`) do the same.

Decoder is in-process via PyAV (bundled FFmpeg, no `brew install` needed). Output via sounddevice/PortAudio (also bundled). Threading model:

- **Opener thread** per `play()` — does the slow part of starting playback (`av.open` for HTTP streams = HTTP connect = 1+ second). The PREVIOUS track keeps playing during the connect, so station switches don't have an audible silence-and-pop.
- **Decoder thread** per playback — reads packets, decodes, resamples, pushes float32 stereo chunks into a bounded queue (~12s buffer).
- **Audio callback** (sounddevice-managed) — drains chunks across `frames` boundaries with carry state, so `frames != _CHUNK_FRAMES` doesn't drop or pad samples.
- **Pre-buffer** — wait for ~186ms of audio before starting the output stream. Without this the first 1-2 callbacks see an empty queue and the user hears "silence-then-pop" attacks.

## Internet radio

When you select the **Radio** entry in the sidebar, the right pane shows a curated list of stations. Default ones are baked into the code (NRK mP3 / P3 / P3 Musikk / Nyheter); you can add your own via `~/.config/musickit/radio.toml`:

```toml
[[stations]]
name = "BBC Radio 6 Music"
url = "https://stream.live.vc.bbcmedia.co.uk/bbc_6music"
description = "BBC's alternative music station"
homepage = "https://www.bbc.co.uk/6music"
```

Defaults baked into code + user TOML are merged at runtime (deduped by URL, user entries win on collision). So the default list only grows when we ship new entries; your file only grows when you add them by hand.

ICY metadata polling: while a stream is playing, the decoder thread polls `container.metadata` per packet for `StreamTitle` updates. The "now playing" pane updates with the live song name as the radio station broadcasts it.

Radio launches as a first-class mode — `musickit tui` with no DIR drops directly into station picking (skipping the library scan entirely).

## Subsonic-client mode

`musickit tui --subsonic URL` makes the TUI talk to any Subsonic-compatible server — your own `musickit serve` over Tailscale, Navidrome, the original Subsonic, etc.

```bash
musickit tui --subsonic http://mlaptop.tail4a4b9a.ts.net:4533 \
             --user mort --password secret
```

Credentials are not persisted — pass `--subsonic` / `--user` / `--password` every time you want client mode.

How it works:

- Launch: 1 (`getArtists`) + N (`getArtist` per artist) calls; ~80 calls for a 800-album library, sub-second over Tailscale. Albums come back as **shells** (metadata + `subsonic_id`, `tracks=[]`).
- Click an album: a `@work(thread=True)` hydrate worker fires `getAlbum?id=...` in the background; the tracklist shows `Loading tracks…` until the response arrives. Hydrated tracks are cached for the rest of the session, so re-opening an album is instant.
- Playback: `track.stream_url` carries the auth-loaded `/rest/stream` URL; `AudioPlayer.play()` hands it straight to PyAV (no different from a radio URL).

Same widgets, same keybindings, same UX as local mode. The only differences are the brief loading delay on first open of each album, and the lack of a local file path (track files exist remotely on the server's disk).

## AirPlay output

Press `a` in the TUI to open the AirPlay picker. It scans the LAN via mDNS, lists discovered devices (HomePods, AirPort Express, Sonos in AirPlay-2 mode, etc.) plus a "Local audio (this Mac)" option that disables routing.

When a device is connected:

- Radio: pass the radio URL straight to the device (it fetches and decodes itself).
- Subsonic-client mode: pass the server's `/rest/stream?id=...` URL with auth.
- Local-file mode: not supported in v1 — falls through to in-process playback (would need an inline HTTP server to serve the local file to the AirPlay device; deferred).

The selected device persists to `state.toml` and auto-resumes on next launch (with a 2s scan; if the device isn't on the LAN we fall back silently to local).

CLI flag for headless / scripted use:

```bash
musickit tui --airplay 'HomePod'                            # exact / substring match by name
musickit tui --airplay '192.168.1.50'                       # match by address
musickit tui --discover                                     # list AirPlay devices (and Subsonic servers) and exit
```

`--airplay` hard-fails if no device matches (you asked for a specific one). The auto-resume path (no `--airplay` flag, `state.toml` has a saved device) skips silently if not found.

## State persistence

`~/.config/musickit/state.toml` holds:

```toml
theme = "tokyo-night"

[airplay]
name = "HomePod"
identifier = "..."
address = "10.0.0.5"
```

Theme persists across all modes. AirPlay block is set when you pick a device from the picker. Subsonic credentials are intentionally not persisted — pass them on the command line each session. Any pre-existing `state.json` is migrated to `state.toml` on first launch and removed.
