# Roadmap

What's open, organized by what it would feel like.

## Tier 1 — listening experience wins

- **Album cover art in the TUI** — small thumbnail in the now-playing header. Tried once with `textual-image` (Unicode halfblock fallback was too low-res); a `chafa` subprocess approach would give crisp output but adds a `brew install chafa` system dep. Deferred until the cost/benefit is clearer.

## Tier 2 — bigger directions (~3-5 sessions)

- **Web UI** — small Vue/htmx frontend mounted at `/` (replacing the JSON probe response). Same Subsonic backend; lets you play from any browser without installing an app.
- **Podcast support** — Subsonic spec already defines `getPodcasts` / `getPodcastEpisode` / `createPodcastChannel`. Add an RSS feed list, fetch episodes on schedule, store position. Symfonium has decent podcast UX out of the box.
- **iTunes / Apple Music import** — read the local Apple Music database to import play counts, ratings, and playlists. One-shot migration tool.
## Tier 3 — server hardening / scale

- **Recorded-session integration tests** — VCR-style HTTP fixtures from real Symfonium / Amperfy / Feishin sessions; replay against `TestClient`. Catches "client X probes new endpoint" regressions.
- **systemd / launchd plist** — for users wanting `serve` to autostart on boot.
- **MQTT / webhook scrobble forwarding** — push play events to Home Assistant / Last.fm / external systems.
- **Bigger `getCoverArt` cache** — currently it's recomputed per request; an LRU keyed on `(album_id, size)` would help on slow disks.
- **Offline browse cache for Subsonic-client mode** — when the TUI is connected, serialise `getArtists` / `getAlbumList` / `getAlbum` responses into a per-server SQLite. On reconnect with a missing server, hydrate the browse panes from cache so you can navigate offline (playback still requires the live server).
- **Scrobble forwarder** — `/scrobble` is a no-op stub today. A configurable webhook + MQTT bridge would push play events to Home Assistant / Last.fm / external scrobble services without dragging in a full scrobble database.
- **TUI `/`-bar filter on top of FTS5** — `/search3` is FTS5-backed (see Done); the TUI's incremental `/` filter is still in-memory casefold substring. Wiring it through the same in-memory FTS5 index would give ranked / prefix-matching results in the TUI.

## Tier 4 — convert-pipeline polish

- **Better folder-fallback for live-venue parens** — currently `(Live in Madrid)` is preserved (correctly), but `Some Album (Live, 2003-04-15, Madrid)` could be cleaned up while still preserving the live-ness signal.
- **AcoustID auto-enable** — currently you have to pass `--acoustid-key` per run. Read it from `~/.config/musickit/serve.toml` once and apply automatically when an album has tagless tracks.
- **Album merge tool** — when the same album exists with different tags as two folders, an interactive merge.
- **`--dry-run` with rich diff** — show exactly what tags would change, what files would move.
- **In-TUI rename when album/artist tags change** — the tag editor patches tags in place but doesn't move the on-disk folder. `retag --rename` does this from the CLI; the TUI editor could too.

## Tier 5 — speculative

Things that would be interesting if anyone ever asked for them, but not pursued speculatively:

- BPM / key analysis (needs `librosa`, big dep weight)
- AI-generated playlists with audio-feature similarity (current `musickit playlist gen` is tag-based; an audio-feature pass would need fingerprinting / `librosa`)
- Multi-user serve (right now: single-user)
- Sonos / Chromecast / DLNA output (AirPlay covers the Apple-ecosystem case)
- Cross-fade between tracks
- Listening rooms / sync-play across clients
- Voice control

## Done

- Convert pipeline — covered in [Convert](guides/convert.md)
- Library audit + fix — covered in [Library](guides/library.md)
- TUI: local + radio modes
- TUI: Subsonic-client mode (lazy-loaded)
- TUI: AirPlay output (CLI + in-TUI picker, persists across launches; pause + volume routed to the device)
- TUI: in-place tag editor — `e` opens a track or album-wide editor that writes via mutagen + patches the in-memory model
- TUI: `/` incremental filter on the focused pane
- TUI: single-click cursor / double-click play (Spotify-style); cursor snaps back to playing track on focus loss
- TUI: ReplayGain normalisation on local playback
- TUI: bordered-panel UI polish (boxed sections, now-playing card, warm accent for active playback)
- TUI: dynamic title-column width that re-flows on terminal resize (debounced)
- TUI: audio engine in a separate process — PyAV decoder + sounddevice callback live in their own interpreter, communicate via `multiprocessing.Queue` + shared memory. UI reflows / focus changes / GC pauses can no longer stall the audio callback into a buffer underrun. Replaces the prior GIL-contention mitigations (500ms / 1s PortAudio buffer, resize-debounce, focus-change short-circuit), now that the engine has its own GIL.
- Subsonic-compatible serve (~30 endpoints)
- XML response format (Subsonic spec default) + POST + form-body credentials
- ffmpeg-on-the-fly transcoding (`format=mp3` / `maxBitRate`)
- OpenSubsonic extensions advertised: `formPost`, `transcodeOffset`, `multipleGenres`, `songLyrics`
- Multi-genre indexing — `byGenre` and `getGenres` honour `track.genres[]`, not just the legacy single-genre field
- Per-track recording MBIDs (one MB `release/<mbid>?inc=recordings` lookup at enrich time)
- mDNS / Bonjour autodiscovery (server advertises, TUI auto-detects)
- Filesystem watcher with debounced auto-rescan
- Persistent SQLite library index — `<root>/.musickit/index.db` removes the cold-start filesystem walk + tag read; `library.load_or_scan` hydrates from rows then runs a delta-validate pass to pick up filesystem changes (added / removed / tag-edited albums); `musickit library index status|drop|rebuild` for management
- Genre indexing (model + scan + `getGenres` + `byGenre`)
- cover-pick semi-automated workflow
- Folder-name edition-annotation strip
- Continuous-numbering across-discs detection
- Real-world tested against Amperfy / Symfonium / play:Sub / Feishin
- Push to GitHub + CI (`make check && make test` on push/PR)
- This documentation site (mkdocs + Material)
- `musickit playlist gen|list|show` — tag-based auto-generated `.m3u8` mixes anchored to a seed track (similarity scorer over artist / genre / year / compilation flag, per-album + per-artist caps; output is standard extended M3U)
- TUI `g` keybind — generate-and-play a 60-min mix from the highlighted or currently-playing track
- TUI Mixes browser entry — saved `.m3u8` files appear in the right pane; selecting one resolves paths against the live index and plays it as a virtual album with stale-path graceful degradation
- Persistent stars / favourites — Subsonic clients' heart buttons are now real. `/star`, `/unstar`, `/getStarred`, `/getStarred2` backed by `<root>/.musickit/stars.toml` (TOML, hand-editable, survives index rebuilds).
- FTS5 search backend — `/search3` and `/search2` use an in-memory SQLite FTS5 index built fresh on every cache reindex. Sub-ms ranked results with prefix matching (`bey` matches `Beyoncé`), diacritic folding (`unicode61 remove_diacritics 2`), bm25 ordering, and multi-token AND across title + album_artist + year body text. Falls back to casefolded substring scan when SQLite was compiled without FTS5.
- Lyrics via LRCLIB — `musickit library lyrics fetch <DIR>` populates `<track>.lrc` sidecars from [lrclib.net](https://lrclib.net) (free, no API key). Sidecars take precedence over embedded `\xa9lyr` / `USLT` / `LYRICS` tags on the next library scan, so user edits stick. The server's `getLyricsBySongId` parses LRC bodies and emits `synced: true` with per-line millisecond offsets — Symfonium / Amperfy display the highlight live. The TUI gains an `l` keybind that swaps the visualizer for a lyrics pane, with the active line bolded as playback advances.
