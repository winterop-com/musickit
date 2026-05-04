# `musickit serve`

A Subsonic-compatible HTTP server. Any modern Subsonic client (Symfonium, Amperfy, play:Sub, Feishin, Supersonic, DSub) connects, browses, searches, streams, and seeks. Works equally well over LAN and over Tailscale.

```bash
musickit serve TARGET_DIR [--host H] [--port P] [--user U] [--password P] [--no-mdns] [--no-watch] [--no-cache] [--full-rescan]
```

`TARGET_DIR` is required. `--host 0.0.0.0`, `--port 4533`, credentials default to `admin`/`admin` with a yellow warning. `--no-cache` skips the persistent SQLite index at `<TARGET_DIR>/.musickit/index.db`; `--full-rescan` rebuilds it from scratch on startup. See [`musickit library`](library.md#persistent-index-db) for index management.

## Startup banner

```
musickit serve — Subsonic API for /Volumes/T9/Output
  bind: 0.0.0.0:4533
  LAN:  http://192.168.1.42:4533
  Tailscale: http://my-mac.tail-scale.ts.net:4533

scanning library…
⠹ Scanning library · 02 - Phenomenon  100/870 ━━━━╸             • 0:00:08
  142 artists, 318 albums, 4521 tracks

  mDNS: advertising as musickit-mlaptop._subsonic._tcp.local
  watching /Volumes/T9/Output for changes (auto-rescan on add/remove/rename)
```

The banner gives you everything you need to point a client at the right URL, and tells you which auto-features are active (mDNS + filesystem watcher).

## Why `--host 0.0.0.0` is the default

Unlike most "self-hosted" services, the default is to bind all interfaces, NOT loopback. Reason: Tailscale assigns each machine a `100.x.x.x` IP that's unreachable from `127.0.0.1`. Binding loopback would make Tailscale access impossible — defeating the whole point.

Auth is mandatory (`admin`/`admin` if you don't override), so the binding is safe even on a public Wi-Fi network. If you really want LAN-blind for some reason:

```bash
musickit serve --host 127.0.0.1
```

## Tailscale walkthrough

The 3-step setup:

1. `tailscale up` on the machine running `serve`.
2. Read the URL from the startup banner (`http://my-mac.tail-scale.ts.net:4533`) — or run `tailscale ip -4` to get the raw IP.
3. In your Subsonic client (Symfonium / Amperfy / Feishin), add a server with that URL + the user/password you set.

That's it. No port forwarding, no HTTPS to set up, no DDNS — Tailscale's WireGuard tunnel handles encryption end-to-end. Reachable from anywhere on your tailnet, anywhere in the world. This was the whole point of binding `0.0.0.0`.

If you don't use Tailscale, the LAN URL works the same way for any device on your local network.

## Recommended clients (2026)

The original `subsonic.org` is dead/abandoned/paid; the actively-maintained client ecosystem orbits **Navidrome's superset** (the OpenSubsonic spec). Tested-against-musickit clients:

**iOS**

- **[Amperfy](https://apps.apple.com/app/amperfy-music/id1530145038)** — FOSS, frequent updates, App Store free
- **[play:Sub](https://apps.apple.com/app/playsub-music-streamer/id955329386)** — paid, very polished
- **[Substreamer](https://apps.apple.com/app/substreamer/id1012991665)** — free with IAP, modern

**Android**

- **[Symfonium](https://symfonium.app/)** — paid one-time (~€8), arguably the best music client on Android right now
- **[Tempo](https://play.google.com/store/apps/details?id=com.cappielloantonio.tempo)** — FOSS / F-Droid, active
- **[Ultrasonic](https://play.google.com/store/apps/details?id=org.moire.ultrasonic)** — FOSS / F-Droid, active

**Desktop**

- **[Feishin](https://github.com/jeffvli/feishin)** — Electron, modern UI
- **[Supersonic](https://github.com/dweymouth/supersonic)** — native Go/Fyne, active

The musickit project also runs as a client itself — `musickit tui --subsonic URL` connects to the same API. See [TUI](tui.md) for that.

## Endpoints implemented

All under `/rest/`. Every endpoint accepts both `GET` and `POST` (some clients prefer one or the other) plus `HEAD` (play:Sub uses it for Content-Length probing).

### System

| Endpoint | Returns |
|---|---|
| `ping` | OK + auth check |
| `getLicense` | Always-valid license stub (Subsonic was paid; clients still check this) |
| `getMusicFolders` | Single `Library` folder |
| `getOpenSubsonicExtensions` | Empty list (we don't claim extensions yet) |

### Browsing (modern, ID3-based)

| Endpoint | Returns |
|---|---|
| `getArtists` | Alphabetically grouped artist list |
| `getArtist?id=` | Albums for one artist, ordered by year |
| `getAlbum?id=` | Album with its tracks |
| `getAlbumList2?type=&size=&offset=` | Flat album list. Types: `alphabeticalByName`, `alphabeticalByArtist`, `random`, `byYear` (with `fromYear`/`toYear`), `byGenre`. Other types fall back to alphabetical. |
| `getSong?id=` | One track |
| `getRandomSongs?size=&fromYear=&toYear=` | Random track sample |

### Browsing (legacy, folder-based)

| Endpoint | Returns |
|---|---|
| `getIndexes` | Alphabetically grouped artist list (legacy envelope shape) |
| `getMusicDirectory?id=` | Routes by ID prefix: `ar_*` → child albums, `al_*` → child songs |

### Search

| Endpoint | Returns |
|---|---|
| `search3?query=&artistCount=&albumCount=&songCount=` | Multi-token AND, case-insensitive substring across artists / albums / titles. Pagination via `*Offset`. |
| `search2?query=...` | Same matching, legacy `searchResult2` envelope key |

### Media

| Endpoint | Returns |
|---|---|
| `stream?id=` | Audio bytes via `FileResponse` with `Accept-Ranges: bytes`. Supports HTTP Range. |
| `stream?id=&format=raw` | Explicit no-transcode |
| `stream?id=&format=mp3` | Transcode via ffmpeg → MP3 (default 192k) |
| `stream?id=&maxBitRate=128` | Cap delivered bitrate (transcodes to MP3 at that rate) |
| `download?id=` | Always raw bytes — ignores `format`/`maxBitRate` per spec |
| `getCoverArt?id=&size=` | Cover image. Sidecar (`cover.jpg`/`folder.jpg`/`front.jpg`) first, embedded picture as fallback. Optional Pillow resize via `?size=N` (capped at 1500). |

### Library control

| Endpoint | Returns |
|---|---|
| `startScan` | Kick a background rescan. Sets `scanning=true` synchronously so a poll right after never sees `scanning=false`. |
| `getScanStatus` | Poll the rescan state |

### Users / preferences

| Endpoint | Returns |
|---|---|
| `getUser?username=` | Single configured user with all roles enabled |
| `getUsers` | List of one user |

### No-op stubs

These are stubs to keep clients quiet on features we don't track yet. They return well-formed empty responses or accept-and-discard.

| Endpoint | Behaviour |
|---|---|
| `scrobble` | Accept + discard |
| `getArtistInfo` / `getArtistInfo2` | Empty bio + similarArtist[] |
| `getStarred` / `getStarred2` | Empty starred set |
| `star` / `unstar` | No-op ok |
| `getPlaylists` / `getPlaylist` | Empty playlist list |
| `getGenres` | Real! Counts songs + distinct albums per genre. |

## Authentication

Three forms supported, all per the Subsonic spec:

- **Plain**: `?u=user&p=password`
- **`enc:` plain**: `?u=user&p=enc:7365637265 74` (some clients send this to avoid logging plain passwords)
- **Salted token**: `?u=user&t=<md5(password+salt)>&s=<salt>` — the modern recommendation

POST requests can put credentials in the form body (`application/x-www-form-urlencoded`); a middleware merges those into the query string before auth runs. play:Sub uses this; without the middleware they'd 401.

The HTTP `Authorization: Basic ...` header is **not** read. If your client has a "Basic Auth" toggle, leave it off.

## Response format

Spec default is XML; `?f=json` opts into JSON. We honour both via a middleware that re-serialises the underlying dict per request. Most modern clients (Symfonium, Feishin, the musickit TUI) send `f=json`; older / iOS clients (Amperfy, play:Sub) often don't and get XML.

## Configuration

```toml
# ~/.config/musickit/serve.toml
username = "mort"
password = "supersecret"
```

Override per-run via `--user` / `--password`. CLI flags win over the TOML.

## mDNS / Bonjour

Default-on: `serve` advertises `_subsonic._tcp.local` so Symfonium / Amperfy / Feishin auto-list the server in their setup screens without anyone typing the URL. Same service type Navidrome uses, so existing client ecosystems pick it up automatically.

The `musickit tui` itself can also discover servers:

```bash
musickit tui --discover                  # list and exit
musickit tui                             # quick browse + show hint
```

`--no-mdns` opts out.

Tailscale users still need to type the tailnet URL once — mDNS doesn't traverse the WireGuard tunnel.

## Filesystem watcher

Default-on: `serve` watches `TARGET_DIR` recursively via `watchdog`. When you drop a new album in, a 5s debounce timer starts; on expiry the cache rescans in the background. Bulk copies of 100 files collapse to one rescan via the timer reset.

Filtering:

- File events: only forwarded for supported audio extensions (`.flac`, `.mp3`, `.m4a`, etc.) — covers, `.DS_Store`, backup files don't trigger.
- Directory events: only `created`/`deleted`/`moved` — `modified` events fire on every child file change and would defeat the audio-extension filter.

`--no-watch` opts out.

## Transcoding

The `stream` endpoint pipes ffmpeg's stdout into the HTTP response when the client asks for transcoding. Decision logic in `_resolve_transcode`:

- `format=raw` → no transcode (explicit opt-out)
- `format=mp3` + non-MP3 source → transcode to MP3 at `maxBitRate` (default 192k)
- `maxBitRate>0` alone → transcode to MP3 at that rate (spec says this caps delivered bitrate)
- everything else → no transcode (default fast path with `Accept-Ranges`)

Subprocess invocation: `ffmpeg -i <path> -vn -c:a libmp3lame -b:a Nk -f mp3 -`. The `-vn` drops the embedded picture stream. The async generator pumps 64 KiB chunks; on client disconnect a `finally` block kills the subprocess so it doesn't pin a CPU.

## Architecture

```
src/musickit/serve/
  app.py             FastAPI factory + envelope helpers + auth dep + middleware
  auth.py            plain / enc: / token verification
  config.py          serve.toml loader + admin/admin defaults
  ids.py             sha1[:16] of artist_dir / album path / track path
  index.py           IndexCache with reverse-lookup dicts
  payloads.py        Subsonic dict builders shared across endpoints
  covers.py          Sidecar-first cover loader + Pillow resize
  xml.py             JSON → Subsonic XML converter
  discovery.py       mDNS register / unregister
  watcher.py         watchdog-backed filesystem watcher
  endpoints/
    system.py        ping / getLicense / getMusicFolders
    browsing.py      getArtists / getArtist / getAlbum / getAlbumList2 / getSong / getIndexes
    search.py        search3 / search2
    media.py         stream / download / getCoverArt
    scan.py          startScan / getScanStatus
    extras.py        scrobble / getArtistInfo / getMusicDirectory / getRandomSongs /
                     getStarred / star / unstar / getPlaylists / getUser / getUsers /
                     getOpenSubsonicExtensions / getGenres
```

The cache is built at startup (synchronously, blocking the CLI before `uvicorn.run`) so the first client request hits a populated index. Background rescans (manual via `/rest/startScan` or automatic via the filesystem watcher) reuse the same `IndexCache.start_background_rescan()` path.
