# `musickit inspect`

Quick tag + cover summary for a single audio file.

```bash
musickit inspect PATH
```

Prints title, artist, album_artist, album, year, genre, track/disc tuples, BPM, label, catalog, lyrics presence, replaygain values, MusicBrainz IDs (if present), and embedded picture dimensions.

Useful when:

- Debugging "why didn't convert pick up X?" — read the source tags directly.
- Verifying an output file got the tags you expected after a `convert` run.
- Spot-checking a single file from a problematic rip before running `library --audit` over the whole library.

```bash
$ uvx musickit inspect ./output/ABBA/1976\ -\ Arrival/01\ -\ When\ I\ Kissed\ The\ Teacher.m4a

Title:         When I Kissed The Teacher
Artist:        ABBA
Album artist:  ABBA
Album:         Arrival
Date:          1976
Genre:         Pop
Track:         1/10
Disc:          1/1
BPM:           113
Cover:         1000×1000  (image/jpeg)
MB album ID:   54bf8f5f-bc55-3dde-9d75-29e26795f29d
```

Backed by `metadata.read_source(path)` — the same tag reader the convert pipeline uses. So what you see here is what convert sees.
