# `musickit retag` / `cover`

Two small in-place edit commands. Useful when the convert pipeline got something subtly wrong, or when you need to retrofit cover art onto already-converted files.

## `musickit retag`

```bash
musickit retag PATH [--title T] [--artist A] [--album-artist AA] [--album AL] \
                    [--year Y] [--genre G] [--track-total N] [--disc-total N]
```

`PATH` can be a single audio file or a directory (recursively walks all supported audio files in it).

Each flag corresponds to a `TagOverrides` field; flags you don't pass are left alone. Pass an empty string to **clear** a tag (e.g. `--genre ''`). Track totals merge with the existing per-track `(track, total)` tuple — the per-track number is preserved.

Backed by `metadata.apply_tag_overrides` which dispatches by extension to MP4 / ID3 / FLAC implementations.

```bash
# Fix a year on one file:
musickit retag path/to/album/01.m4a --year 1976

# Re-tag a whole album to set total-tracks:
musickit retag path/to/album --track-total 12

# Clear a wrong genre across an album:
musickit retag path/to/album --genre ''
```

## `musickit cover`

```bash
musickit cover IMAGE TARGET_DIR [--cover-max-edge PX] [--recursive/--no-recursive]
```

Embeds `IMAGE` into every audio file under `TARGET_DIR`. Other tags are preserved — only the cover is replaced. The image is normalised once (Pillow decode → resize to fit long-edge cap → JPEG-encode for non-PNG inputs) and reused across all writes.

```bash
musickit cover scan-of-the-LP.jpg ./output/Pink\ Floyd/1973\ -\ The\ Dark\ Side\ Of\ The\ Moon
```

Defaults: `--cover-max-edge 1000`, `--recursive` on (so multi-disc albums get covered in one shot).

## When to use which

- **You have the cover image already** (downloaded from somewhere, scanned an LP, etc.) → `musickit cover IMAGE DIR`
- **You want the semi-automated picker** (browse musichoarders, pick interactively) → `musickit cover-pick` (see [Library](library.md))
- **A track has the wrong title / wrong year / etc.** → `musickit retag PATH --year ...`

If you need to do a lot of these, the better workflow is usually:

1. `musickit library ./output --audit` — find what's broken
2. `musickit library ./output --fix` — auto-fix what can be auto-fixed (missing year via MB, tag/path rename)
3. `musickit cover-pick ./output` — semi-automated cover sourcing for the flagged ones
4. `musickit retag path/to/specific/album --whatever ...` — handle the residual edge cases manually
