# Roadmap

What's open vs. shipped.

## Open — convert pipeline polish

- **Better folder-fallback for live-venue parens** — currently `(Live in Madrid)` is preserved (correctly), but `Some Album (Live, 2003-04-15, Madrid)` could be cleaned up while still preserving the live-ness signal.
- **AcoustID auto-enable** — currently you have to pass `--acoustid-key` per run. Read it from `~/.config/musickit/serve.toml` once and apply automatically when an album has tagless tracks.
- **Album merge tool** — when the same album exists with different tags as two folders, an interactive merge.
- **`--dry-run` with rich diff** — show exactly what tags would change, what files would move.

## Speculative

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
- TUI: `/`-bar filter — diacritic-folded **multi-token AND** substring on the focused pane. `daft homework` finds `Daft Punk - Homework` even though the words aren't adjacent in the row; `bey` still finds `Beyoncé`; `sigur ros` still finds `Sigur Rós`. Per-pane filtering on a 1200-row library remains microseconds — no FTS index needed in the TUI process.
- TUI: single-click cursor / double-click play (Spotify-style); cursor snaps back to playing track on focus loss
- TUI: ReplayGain normalisation on local playback
- TUI: bordered-panel UI polish (boxed sections, now-playing card, warm accent for active playback)
- TUI: dynamic title-column width that re-flows on terminal resize (debounced)
- TUI: audio engine in a separate process — PyAV decoder + sounddevice callback live in their own interpreter, communicate via `multiprocessing.Queue` + shared memory. UI reflows / focus changes / GC pauses can no longer stall the audio callback into a buffer underrun. Replaces the prior GIL-contention mitigations (500ms / 1s PortAudio buffer, resize-debounce, focus-change short-circuit), now that the engine has its own GIL.
- TUI tag editor folder rename — saving the album-wide editor with a changed `Album`, `Album Artist`, or `Year` field now also moves the on-disk folder to match (`<root>/<artist_dir>/YYYY - <Album>`). Cross-artist edits move the album under the new artist parent dir. Genre-only edits don't trigger a rename. Collisions stay safe — the modal stays open with a warning instead of partial state.
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
- `getCoverArt` LRU cache — bytes-bounded (default 64 MiB) in-memory LRU keyed on `(id, size)`. Mobile clients hammer thumbnails one-per-row when paging through albums; the cache turns the second-through-Nth request into a dict lookup instead of a sidecar read + Pillow decode. Invalidated on every `_reindex` so a re-cover-picked album doesn't serve stale bytes.
- `getCoverArt` SVG placeholder for missing art — instead of a 404 / error envelope, the server returns a 256x256 `♪` glyph SVG. Means every Subsonic client (Feishin / play:Sub / others without their own fallback) renders something sensible. Navidrome takes the same approach.
- Anyio thread pool 40 → 256 — Starlette's `FileResponse` reads each file in a worker thread; one chatty client (play:Sub on iOS opens 30-50 parallel `/rest/stream` connections) used to exhaust the default 40-thread cap and freeze every other request. 256 leaves room for one chatty client without affecting normal operation.
- Scrobble forwarder — `/scrobble` is no longer a no-op stub. With `[scrobble.webhook]` (POST JSON) and/or `[scrobble.mqtt]` (publish topic) in `serve.toml`, every Subsonic client's play event is forwarded — usable for Home Assistant, Last.fm bridges, ListenBrainz, custom analytics. Fire-and-forget on a small thread pool; failures are logged + swallowed so a dead bridge can't 500 the client. Defaults to `submission=true`-only events; flip `include_now_playing` to receive both kinds.
- Browser UI — full TUI-alike web player at `/web`, hand-rolled vanilla JS + CSS, no bundler. Bordered panels with floating titles, the same palette and density as the TUI, ncmpcpp-style KeyBar, marquee scroll for long titles. Login + signed session cookie so `<audio src="/rest/stream?id=...">` can stream without leaking the password into HTML.
- Browser UI: search, lyrics pane, FFT visualizer (Web Audio API + Canvas), command palette (Cmd/Ctrl+P), help slide-in (`?`), keybinds for repeat / shuffle / volume / seek / fullscreen / lyrics / palette. Dynamic palette + keybar + help filter their contents by playback mode (track vs. radio) so radio listeners don't see Repeat / Shuffle / Seek that don't apply.
- Browser UI: internet radio. The Subsonic `getInternetRadioStations` endpoint now serves the same stations the TUI plays (defaults + `~/.config/musickit/radio.toml`); the web UI lists them in a Radio sidebar panel and plays via a same-origin `/web/radio-stream` proxy. The proxy parses inline ICY metadata and exposes the current StreamTitle via `/web/radio-meta` for the Now Playing card to poll. Symfonium / Amperfy / play:Sub also pick up the same station list.
- Browser UI: cover-art `♪` placeholder — every cover-less album row + Now Playing card renders a centered `♪` glyph on a slightly-elevated grey square. CSS background under an `<img>`, with a transparent SVG fallback so Chromium's broken-image marker doesn't show through.
- `musickit serve --no-web` — disables the bundled browser UI when only the Subsonic `/rest/*` API is wanted (smaller attack surface; headless / embedded deployments).
