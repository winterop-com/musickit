# Roadmap

What's open, organized by what it would feel like.

## Tier 1 — listening experience wins (~1 session each)

- **ReplayGain on stream** — track / album gain values are already in tags; apply as software gain in `AudioPlayer._audio_callback` and as `-af volume=Ndb` when transcoding. Eliminates loud-soft jumps between tracks.
- **Lyrics via LRCLIB** — read embedded `\xa9lyr` (MP4) / `USLT` (ID3); fetch from [lrclib.net](https://lrclib.net) for missing ones (free, no API key, supports synced LRC). Cache per-track as `<track>.lrc` sidecar. Expose via Subsonic `getLyrics` / `getLyricsBySongId`. Symfonium + Amperfy display synced lyrics live.
- **Per-track recording MBIDs** — currently only `album_id` / `artist_id` / `release_group_id` are populated from MB. One follow-up `release/<mbid>?inc=recordings` lookup would map MB recordings to our tracks.

## Tier 2 — bigger directions (~3-5 sessions)

- **Web UI** — small Vue/htmx frontend mounted at `/` (replacing the JSON probe response). Same Subsonic backend; lets you play from any browser without installing an app.
- **Podcast support** — Subsonic spec already defines `getPodcasts` / `getPodcastEpisode` / `createPodcastChannel`. Add an RSS feed list, fetch episodes on schedule, store position. Symfonium has decent podcast UX out of the box.
- **iTunes / Apple Music import** — read the local Apple Music database to import play counts, ratings, and playlists. One-shot migration tool.

## Tier 3 — server hardening / scale

- **Push to GitHub + CI** — `gh repo create musickit --public --source . --push` + `.github/workflows/ci.yml` running `make lint && make test`. Backup, contribution discoverability, regression catching.
- **OpenSubsonic extensions advertisement** — `getOpenSubsonicExtensions` returns `[]` today; should advertise what we actually do (transcodeOffsets, multi-genre, etc.).
- **Recorded-session integration tests** — VCR-style HTTP fixtures from real Symfonium / Amperfy / Feishin sessions; replay against `TestClient`. Catches "client X probes new endpoint" regressions.
- **systemd / launchd plist** — for users wanting `serve` to autostart on boot.
- **MQTT / webhook scrobble forwarding** — push play events to Home Assistant / Last.fm / external systems.

## Tier 4 — convert-pipeline polish

- **Better folder-fallback for live-venue parens** — currently `(Live in Madrid)` is preserved (correctly), but `Some Album (Live, 2003-04-15, Madrid)` could be cleaned up while still preserving the live-ness signal.
- **AcoustID auto-enable** — currently you have to pass `--acoustid-key` per run. Read it from `~/.config/musickit/serve.toml` once and apply automatically when an album has tagless tracks.
- **Album merge tool** — when the same album exists with different tags as two folders, an interactive merge.
- **`--dry-run` with rich diff** — show exactly what tags would change, what files would move.

## Tier 5 — speculative

Things that would be interesting if anyone ever asked for them, but not pursued speculatively:

- AI-generated playlists / mood tagging
- BPM / key analysis (needs `librosa`, big dep weight)
- Multi-user serve (right now: single-user)
- Sonos / Chromecast / DLNA output (AirPlay covers the Apple-ecosystem case)
- Cross-fade between tracks
- Listening rooms / sync-play across clients
- Voice control

## Done

- ✅ Convert pipeline — covered in [Convert](guides/convert.md)
- ✅ Library audit + fix — covered in [Library](guides/library.md)
- ✅ TUI: local + radio modes
- ✅ TUI: Subsonic-client mode (lazy-loaded)
- ✅ TUI: AirPlay output (CLI + in-TUI picker, persists across launches)
- ✅ Subsonic-compatible serve (~30 endpoints)
- ✅ XML response format (Subsonic spec default)
- ✅ POST + form-body credential support
- ✅ ffmpeg-on-the-fly transcoding (`format=mp3` / `maxBitRate`)
- ✅ mDNS / Bonjour autodiscovery (server advertises, TUI auto-detects)
- ✅ Filesystem watcher with debounced auto-rescan
- ✅ Genre indexing (model + scan + `getGenres` + `byGenre`)
- ✅ cover-pick semi-automated workflow
- ✅ Folder-name edition-annotation strip
- ✅ Continuous-numbering across-discs detection
- ✅ Real-world tested against Amperfy / Symfonium / play:Sub / Feishin
- ✅ This documentation site (mkdocs + Material)
