# `musickit playlist`

Auto-generated `.m3u8` playlists anchored to a seed track. Tag-based similarity only — no audio fingerprinting, no remote lookups, no play-history dependency. Available since v0.6.0; TUI integration since v0.6.1; saved-mixes browser since v0.6.2.

## Quick start

```bash
uvx musickit playlist gen ~/Music \
    --seed "~/Music/Pixies/1989 - Doolittle/01 - Debaser.m4a" \
    --minutes 60
```

Output:

```
Generated Mix - Pixies - Debaser (24 tracks, 61.2 min / target 60 min)
→ ~/Music/.musickit/playlists/mix-pixies-debaser.m3u8
```

The generated `.m3u8` is a plain extended M3U with `#EXTINF` lines, so VLC, mpv, every Subsonic client, and the MusicKit TUI all open it directly.

## Subcommands

```
musickit playlist gen ROOT --seed PATH [--minutes 60] [--name NAME] [--out PATH] [--random-seed N]
musickit playlist list ROOT
musickit playlist show ROOT NAME
```

### `gen` — generate a mix

| Option | Default | Description |
|---|---|---|
| `--seed PATH` | required | Seed track. Absolute path, or a bare filename present in the library. |
| `--minutes N` | `60.0` | Target playlist length in minutes. |
| `--out PATH` | `<ROOT>/.musickit/playlists/<slug>.m3u8` | Output file. Slug is derived from `--name` (or from the seed if no name). |
| `--name NAME` | `Mix - <artist> - <title>` | Display name written into `#PLAYLIST:` header. |
| `--random-seed N` | unseeded | Seed the tie-breaker RNG so repeat invocations produce identical output (useful for debugging or sharing). |
| `--no-cache` | off | Skip the SQLite library index; in-memory scan only. |
| `--full-rescan` | off | Force a full filesystem walk + tag re-read before generating. |

### `list` — show saved mixes

```bash
uvx musickit playlist list ~/Music
```

Renders a table of every `.m3u8` under `<ROOT>/.musickit/playlists/`, with track counts and on-disk paths.

### `show` — print a saved mix's tracks

```bash
uvx musickit playlist show ~/Music mix-pixies-debaser
```

Prints the resolved track paths from the `.m3u8` with `[y]` / `[n]` markers showing whether each file still exists on disk. Useful for sanity-checking a saved mix after moving / renaming files.

## How the similarity score works

For each candidate track, `score(seed, candidate)` adds:

| Signal | Weight | Notes |
|---|---|---|
| Same `album_artist` | **+5.0** | Falls back to `artist` if `album_artist` is empty. The strongest signal — anchors the mix to the same band. |
| Genre token overlap | **+3.0** | Tokens split on whitespace + `/, ; & -`, lowercased. So `"Indie Rock / Alternative"` and `"Alternative Rock"` overlap on `rock`. |
| Same year | +2.0 | |
| Within 5 years | +1.0 | |
| Within 15 years | +0.5 | |
| Compilation / non-compilation mismatch | **-3.0** | "Various Artists" + non-VA seed (or vice versa). Avoids weird cross-pollution. |
| Track equals seed | -∞ | Short-circuit so the seed never picks itself. |

After scoring, the builder walks the candidates high-to-low and picks the top one that doesn't violate:

- **Per-album cap** = 2 (avoids "shuffle dumps a whole album in").
- **Per-artist cap** = 4 (a 60-min mix shouldn't be 12 tracks by one band).

It stops when the accumulated duration meets or exceeds the target. Scoring is against the seed, not the last-picked track, so the playlist stays anchored to a coherent feel instead of drifting genre-by-genre.

## Output format

Standard extended M3U:

```
#EXTM3U
#PLAYLIST:Mix - Pixies - Debaser
#EXTINF:181,Pixies - Debaser
../../Pixies/1989 - Doolittle/01 - Debaser.m4a
#EXTINF:200,Pixies - Tame
../../Pixies/1989 - Doolittle/02 - Tame.m4a
...
```

Paths inside the file are computed with `Path.relative_to(walk_up=True)` (Python 3.12+). The playlist file at `<root>/.musickit/playlists/foo.m3u8` references tracks via `../../Artist/Album/track.m4a` — works in every audio player and survives moving the whole library tree as a unit. Cross-filesystem paths fall back to absolute.

## TUI integration

### `g` — generate-and-play (since v0.6.1)

While focused on the TrackList, press `g`. The action:

1. Resolves the seed (highlighted row, falls back to currently-playing track).
2. Calls `playlist.generate(index, seed, target_minutes=60)`.
3. Persists the result to `<root>/.musickit/playlists/<slug>.m3u8` for cross-tool reuse.
4. Wraps the result as a virtual `LibraryAlbum` (`artist_dir = "Mix"`) and starts playback. All the existing playback machinery (next / prev / shuffle / repeat) works on the mix as if it were a regular album.

### Mixes browser entry (since v0.6.2)

The sidebar now shows `♫ Mixes (N)` next to `📻 Radio`. Selecting it populates the right-pane TrackList with one row per saved mix (name + track count). Enter / double-click on a row replays the mix.

Tracks whose paths no longer resolve (file renamed / moved since the mix was generated) are silently skipped — the mix degrades gracefully. If every track is gone, you get a notification and nothing changes.

## Use cases

- **Rediscover a forgotten album.** Seed from a track you remember; let the mix surface adjacent tracks you haven't played in a while.
- **Generate a session for the road.** `--minutes 90` produces a coherent ~1.5h mix for a drive.
- **Share with another player.** The `.m3u8` works in VLC, mpv, every Subsonic client. Copy it into a Subsonic playlists folder and any client picks it up via `getPlaylist`.
- **Reproducible mixes.** `--random-seed 42` makes the output identical across runs, so you can share a "seed track + seed number" recipe with someone else's library and expect the same picks.

## What it deliberately doesn't do (yet)

- **No play history.** "Recently played" / "forgotten" / "discover never-played" smart playlists are gated on a `play_history` table, which lands later as Phase 2.
- **No audio-feature similarity.** No BPM, key, energy, or fingerprinting. Tag-based scoring is good enough for personal libraries and doesn't drag in `librosa` / `essentia`.
- **No mix editing UI.** Want to remove a track from a saved mix? Open the `.m3u8` in your editor — it's plain text. The TUI's Mixes view is read + replay only.
- **No cross-library mixes.** The seed must be in the library you're scanning; mixes can't span multiple roots.

## Edge cases

- **Seed not found.** `gen` exits 1 with `Error: seed not found in library index: ...`. The argument can be an absolute path or a bare filename — the resolver tries both.
- **Library smaller than the target duration.** The mix is just shorter; no error. Real libraries hit per-album / per-artist caps long before exhausting the candidate pool.
- **Subsonic-client mode in the TUI.** The `g` keybind warns and no-ops. There's no local index to score against in client mode; generate from the server-side library through the standard `gen` CLI instead.
- **Stale paths in a saved `.m3u8`.** `_load_mix` resolves each path against the current index and skips anything that no longer matches. If every path is stale, the mix can't be replayed and the user is notified.
