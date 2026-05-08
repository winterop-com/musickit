# Roadmap

What's open vs. speculative.

## Open — convert pipeline polish

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
