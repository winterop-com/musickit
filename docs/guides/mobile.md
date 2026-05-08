# Mobile

MusicKit's `serve` command exposes the [Subsonic API], so any
Subsonic-compatible mobile app can stream from your library — there's no
MusicKit mobile app to install.

This guide walks through the four clients that work well in 2026.

## TL;DR

1. Run `musickit serve` somewhere your phone can reach (Tailscale, LAN, or
   a public URL behind a reverse proxy).
2. Install one of the apps below.
3. Point it at `http(s)://<host>:4533/rest`, with the username + password
   from `~/.config/musickit/musickit.toml` (or `MUSICKIT_SERVER__USERNAME`
   / `MUSICKIT_SERVER__PASSWORD`).
4. Stream.

## Prerequisites

The server itself is covered in [the serve guide](serve.md). For mobile
the things to double-check are:

- **Reachable from the phone.** Localhost won't work; the phone needs to
  resolve the host. The easiest answer is [Tailscale] (the phone joins
  the same tailnet as the server, then connects to
  `http://<machine-name>:4533`). LAN works too if the phone is on
  the same Wi-Fi.
- **Username + password set.** The default `admin` / `admin` will
  authenticate, but every client persists credentials, so use the same
  pair you set in `musickit.toml`.
- **HTTPS if leaving the LAN.** Subsonic auth is salted-token, but the
  audio stream is plaintext bytes. Put the server behind a Caddy /
  Tailscale Funnel / nginx terminator before exposing it on the public
  internet.

## iOS

### play:Sub

Polished, free, supported. Best general-purpose client.

1. Install [play:Sub](https://apps.apple.com/app/play-sub/id955329386)
   from the App Store.
2. Settings → Servers → Add Server.
3. Fill in:
   - **Server Address**: `http://<host>:4533` (no trailing `/rest`)
   - **Username**: from `musickit.toml`
   - **Password**: from `musickit.toml`
4. Save. play:Sub does the salted-token handshake; if the credentials
   are right, "Test Connection" succeeds.
5. Browse → tap album → play.

### Amperfy

Free, open source, scrobble-aware. Good if you also use Last.fm.

1. Install [Amperfy](https://apps.apple.com/app/amperfy/id1530145105).
2. Settings → Server → Add Server. Same fields as above.
3. Amperfy defaults to JSON (`f=json`); MusicKit serves both — no
   tweak needed.

## Android

### Symfonium

Paid (~5 EUR), best-in-class UI.

1. Install [Symfonium](https://play.google.com/store/apps/details?id=app.symfonik.music_player)
   from Play.
2. Settings → Sources → Add → Subsonic.
3. URL: `http://<host>:4533`, plus user + password.
4. Save → Sync. Syncing pulls a metadata index; subsequent browsing is
   offline-aware.

### DSub

Free, open source, the Subsonic veteran. Older UI but rock-solid.

1. Install [DSub](https://f-droid.org/en/packages/github.daneren2005.dsub/)
   from F-Droid (the Play version is years out of date).
2. Settings → Servers → Server 1 → enable + fill in the same fields.

### Tempo

Free, open source, works on phone + Android Auto.

1. Install [Tempo](https://github.com/CappielloAntonio/tempo) from
   F-Droid or GitHub.
2. Login screen takes the same URL + credentials.

## Troubleshooting

### "Could not connect"

- Confirm the server is reachable from the phone:
  `curl http://<host>:4533/rest/ping?u=<user>&p=<password>` from a
  laptop on the same network.
- Don't include `/rest` in the URL field — the apps append it.

### "Invalid credentials"

- The Subsonic spec uses salted-token auth (`md5(password + salt)`),
  not plain password. All four apps handle this transparently — but if
  you've set `MUSICKIT_SERVER__PASSWORD` to a long random token,
  occasional clients hash incorrectly. A short alphanumeric password
  works around this.

### "No music shows up"

- The server only sees what's under the path you launched it with:
  `musickit serve /path/to/Music`. Verify
  `curl 'http://<host>:4533/rest/getArtists?u=...&p=...&f=json'` returns
  a non-empty list.
- If `getArtists` returns content but the app's empty, force-rescan
  inside the app (most have a "Sync" or "Refresh server" action).

### Cover art looks low-res

- Subsonic apps fetch cover art via `/rest/getCoverArt?id=...&size=N`.
  MusicKit serves the original embedded artwork; if your library has
  small embeds, the upscale is what the app sees. Embed at 1000×1000
  via [convert](convert.md) for crisp art.

## What MusicKit doesn't do (yet)

- **No native iOS / Android app of its own.** The Subsonic ecosystem
  is solid; we'd rather you use a polished third-party client than ship
  a 1.0 MusicKit app that's worse than play:Sub or Symfonium.
- **No CarPlay / Android Auto direct.** Tempo (Android) supports
  Android Auto. CarPlay routing on iOS depends on the client; play:Sub
  works.
- **No background download / offline cache support in MusicKit itself.**
  Each client handles caching independently — most cache by default.

[Subsonic API]: https://www.subsonic.org/pages/api.jsp
[Tailscale]: https://tailscale.com
