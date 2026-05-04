# `musickit inspect`

Quick tag + cover summary for a single audio file.

```bash
uvx musickit inspect PATH
uvx musickit inspect PATH --json   # raw model dump for scripting / jq
```

Prints title, artist, album_artist, album, year, genre, track/disc tuples, BPM, label, catalog, lyrics, replaygain values, MusicBrainz IDs (if present), and embedded picture dimensions, all as labelled rich panels — File, Tags, ReplayGain (when present), Lyrics (when present), Embedded picture (when present). Empty fields are suppressed so the output reflects only what's actually tagged.

Useful when:

- Debugging "why didn't convert pick up X?" — read the source tags directly.
- Verifying an output file got the tags you expected after a `convert` run.
- Spot-checking a single file from a problematic rip before running `library audit` over the whole library.

Sample output:

```
─── File ───────────────────────────────────────
  path      ./output/ABBA/1976 - Arrival/01 - When I Kissed The Teacher.m4a
  size      6.2 MiB
  format    M4A
  duration  3:00

─── Tags ───────────────────────────────────────
  title         When I Kissed The Teacher
  artist        ABBA
  album artist  ABBA
  album         Arrival
  date          1976
  track         1/10
  disc          1/1
  genres        Pop
  bpm           113

─── Embedded picture ───────────────────────────
  mime    image/jpeg
  size    245.0 KiB
  pixels  ~1,000,000 px
```

Pass `--json` to skip the Rich rendering and get the raw `SourceTrack` model serialised for downstream pipes (`uvx musickit inspect track.m4a --json | jq .title`).

Backed by `metadata.read_source(path)` — the same tag reader the convert pipeline uses. So what you see here is what convert sees.
