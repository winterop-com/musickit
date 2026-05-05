#!/usr/bin/env python3
"""Generate SVG screenshots of the musickit TUI for the docs.

Builds a small fixture library of silent tagged tracks, drives
`MusickitApp` headlessly via Textual's `Pilot`, presses key sequences to
reach interesting states, and exports each as an SVG into
`docs/screenshots/`.

Run via:

    make docs-screenshots

or directly:

    uv run python scripts/gen_screenshots.py

The output SVGs are committed to the repo and embedded in `tui.md` /
`tutorial.md` etc. They render crisply at any zoom and are
text-searchable by web crawlers.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

# ---------------------------------------------------------------------------
# Fixture library
# ---------------------------------------------------------------------------

# A handful of well-known artists / albums so the screenshots look like a
# real library. Tracks are silent (0.2s AAC) but properly tagged. Cover is
# a solid colour per album so the visual output stays distinguishable
# without bundling artwork into the repo.

ALBUMS: list[tuple[str, str, str, list[tuple[str, str, int]]]] = [
    # (artist, "YYYY - Album", colour, [(filename, title, track_no)])
    (
        "Bee Gees",
        "1969 - Best Of",
        "darkred",
        [
            ("01 - Massachusetts.m4a", "Massachusetts", 1),
            ("02 - I Started A Joke.m4a", "I Started A Joke", 2),
            ("03 - To Love Somebody.m4a", "To Love Somebody", 3),
            ("04 - Words.m4a", "Words", 4),
            ("05 - How Deep Is Your Love.m4a", "How Deep Is Your Love", 5),
        ],
    ),
    (
        "Imagine Dragons",
        "2012 - Night Visions",
        "darkblue",
        [
            ("01 - Radioactive.m4a", "Radioactive", 1),
            ("02 - Tiptoe.m4a", "Tiptoe", 2),
            ("03 - Demons.m4a", "Demons", 3),
            ("04 - On Top Of The World.m4a", "On Top Of The World", 4),
        ],
    ),
    (
        "Lauryn Hill",
        "1998 - The Miseducation Of Lauryn Hill",
        "darkgreen",
        [
            ("01 - Lost Ones.m4a", "Lost Ones", 1),
            ("02 - Doo Wop (That Thing).m4a", "Doo Wop (That Thing)", 2),
            ("03 - Ex-Factor.m4a", "Ex-Factor", 3),
            ("04 - To Zion.m4a", "To Zion", 4),
        ],
    ),
    (
        "Pink Floyd",
        "1973 - The Dark Side Of The Moon",
        "purple",
        [
            ("01 - Speak To Me.m4a", "Speak To Me", 1),
            ("02 - Breathe.m4a", "Breathe (In The Air)", 2),
            ("03 - Time.m4a", "Time", 3),
            ("04 - Money.m4a", "Money", 4),
            ("05 - Us And Them.m4a", "Us And Them", 5),
        ],
    ),
    (
        "Radiohead",
        "1997 - OK Computer",
        "darkorange",
        [
            ("01 - Airbag.m4a", "Airbag", 1),
            ("02 - Paranoid Android.m4a", "Paranoid Android", 2),
            ("03 - Karma Police.m4a", "Karma Police", 3),
            ("04 - No Surprises.m4a", "No Surprises", 4),
        ],
    ),
]


def _silent_m4a(out: Path) -> None:
    """Write a 0.2s silent stereo AAC m4a via ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "0.2",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out),
        ],
        check=True,
    )


def _write_track(
    src_silent: Path,
    album_dir: Path,
    *,
    filename: str,
    title: str,
    artist: str,
    album: str,
    year: str,
    track_no: int,
    track_total: int,
    cover_colour: str,
) -> None:
    """Copy `src_silent` into `album_dir/filename` and write tags + cover."""
    import shutil

    album_dir.mkdir(parents=True, exist_ok=True)
    dst = album_dir / filename
    shutil.copy2(src_silent, dst)

    mp4 = MP4(dst)
    if mp4.tags is None:
        mp4.add_tags()
    tags = mp4.tags
    assert tags is not None
    tags["\xa9nam"] = [title]
    tags["\xa9ART"] = [artist]
    tags["aART"] = [artist]
    tags["\xa9alb"] = [album]
    tags["\xa9day"] = [year]
    tags["trkn"] = [(track_no, track_total)]

    img = Image.new("RGB", (800, 800), color=cover_colour)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    tags["covr"] = [MP4Cover(buf.getvalue(), imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()


def build_fixture_library(root: Path) -> None:
    """Populate `root` with the canned ALBUMS list above."""
    silent = root / "_silent.m4a"
    _silent_m4a(silent)
    for artist, album_dir_name, colour, tracks in ALBUMS:
        # Strip the "YYYY - " prefix to derive the album tag.
        _, album_tag = album_dir_name.split(" - ", 1)
        year = album_dir_name[:4]
        album_dir = root / artist / album_dir_name
        for filename, title, track_no in tracks:
            _write_track(
                silent,
                album_dir,
                filename=filename,
                title=title,
                artist=artist,
                album=album_tag,
                year=year,
                track_no=track_no,
                track_total=len(tracks),
                cover_colour=colour,
            )
    silent.unlink()


# ---------------------------------------------------------------------------
# Screenshot driver
# ---------------------------------------------------------------------------

# Width × height. 200 wide is generous so labels don't truncate; 50 tall
# fits the now-playing card + visualizer + tracklist + status bar.
SCREENSHOT_SIZE = (200, 50)


async def _wait_for_scan(pilot: object, timeout: float = 10.0) -> None:
    """Block the test pilot until the library scan has populated the index."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pilot.app._index is not None:  # type: ignore[attr-defined]
            await pilot.pause()  # type: ignore[attr-defined]
            return
        await pilot.pause(0.05)  # type: ignore[attr-defined]


def _fake_spectrum(n_bands: int = 48) -> list[float]:
    """Return plausible-looking visualizer band heights.

    No real playback needed for the docs screenshots — we just inject
    these values into the Visualizer widget's `levels` reactive so the
    bars render as if music were playing. Faster than driving the audio
    engine, and works on CI hosts without an audio device.

    Shape: bass-heavy left side (deep bass + kick energy), gentle slope
    through mids, sharp drop into the top octaves with a few small
    peaks (cymbals / sibilance) for visual interest.
    """
    import math

    levels: list[float] = []
    # Hand-tuned anchor points; intermediate bands interpolated in log-x.
    # Each tuple is (band_index, height).
    anchors = [(0, 0.92), (3, 0.86), (8, 0.74), (16, 0.58), (24, 0.46), (32, 0.30), (40, 0.18), (47, 0.06)]
    # Decorative little peaks scattered across the higher bands so the
    # right side doesn't decay into a flat slope.
    peaks = {6: 0.95, 14: 0.82, 21: 0.72, 27: 0.58, 35: 0.42}
    for i in range(n_bands):
        # Find the two anchors bracketing band i.
        for j in range(len(anchors) - 1):
            a_idx, a_h = anchors[j]
            b_idx, b_h = anchors[j + 1]
            if a_idx <= i <= b_idx:
                # Linear interp between anchors.
                t = (i - a_idx) / max(1, b_idx - a_idx)
                base = a_h + (b_h - a_h) * t
                break
        else:  # pragma: no cover
            base = 0.0
        # Tiny smooth ripple so adjacent bars aren't perfectly monotonic.
        base += 0.03 * math.sin(i * 0.7)
        if i in peaks:
            base = peaks[i]
        levels.append(max(0.0, min(1.0, base)))
    return levels


async def _capture(
    name: str,
    root: Path,
    out_dir: Path,
    keys: list[str],
    note: str,
    *,
    show_viz: bool = False,
) -> None:
    """Drive a fresh `MusickitApp`, press `keys`, save the SVG screenshot.

    `show_viz=True` injects fake band levels into the Visualizer widget
    after the keypresses so the screenshot shows the bars at musical
    heights instead of all-zero (which is what real playback of the
    silent fixture tracks produces).
    """
    from musickit.tui.app import MusickitApp

    print(f"  [{name}] {note}")
    app = MusickitApp(root)
    async with app.run_test(size=SCREENSHOT_SIZE) as pilot:
        await _wait_for_scan(pilot)
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        if show_viz:
            # Inject fake band levels into the player's shared-memory
            # array. The Visualizer's `levels` reactive is rewritten on
            # every UI tick from `self._player.band_levels`, so setting
            # the widget directly gets clobbered ~33ms later. Writing
            # into the shared mem is the source of truth.
            shared = app._player._state.band_levels  # type: ignore[attr-defined]
            fake = _fake_spectrum()
            with shared.get_lock():
                for i, v in enumerate(fake):
                    shared[i] = v
            # Also pin the playing-track marker / progress bar so the
            # `▶ 02:14 ━━━━━━━━━━━━━━━━━━━━━━━━ 04:30` line shows live
            # values instead of `■ 00:00 ░░░░ 00:00`. These all live in
            # the same shared mem block.
            state = app._player._state  # type: ignore[attr-defined]
            with state.duration_s.get_lock():
                state.duration_s.value = 270.0  # 4:30
            with state.position_frames.get_lock():
                state.position_frames.value = 134 * 44100  # 2:14
            with state.stopped.get_lock():
                state.stopped.value = 0
            with state.paused.get_lock():
                state.paused.value = 0
            await pilot.pause()
        # Two extra pauses give the visualizer / progress / etc. one render
        # cycle to settle on whatever final state the keypresses produced.
        await pilot.pause()
        await pilot.pause()
        svg = app.export_screenshot(title=f"musickit · {name}")
    (out_dir / f"{name}.svg").write_text(svg)


async def main() -> None:
    """Build the fixture library and capture every TUI screen as an SVG."""
    out_dir = Path("docs/screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="musickit-screenshots-") as td:
        root = Path(td)
        print(f"Building fixture library at {root} ...")
        build_fixture_library(root)
        print(f"  {sum(len(a[3]) for a in ALBUMS)} tracks across {len(ALBUMS)} albums")

        # 1. Initial browse view — artist list. Pilot needs no keys; the
        #    app starts on the artists pane.
        await _capture(
            "browse-artists",
            root,
            out_dir,
            keys=[],
            note="Initial browse — artist list",
        )

        # 2. Inside an album — drill into Pink Floyd → DSOTM. Browser
        #    cursor: Bee Gees(0) → Imagine Dragons(1) → Lauryn Hill(2)
        #    → Pink Floyd(3). Down 4 from the Radio (row 0), enter to
        #    drill, enter to open the (sole) album, focus moves to
        #    tracklist. show_viz injects fake band levels so the
        #    screenshot shows musical bars instead of silence.
        await _capture(
            "album-tracks",
            root,
            out_dir,
            keys=["down", "down", "down", "down", "enter", "enter"],
            note="Drilled-in album view (with viz)",
            show_viz=True,
        )

        # 3. Fullscreen visualizer — same as #2 plus `f`. show_viz on so
        #    the giant bars actually have heights.
        await _capture(
            "fullscreen-viz",
            root,
            out_dir,
            keys=["down", "down", "down", "down", "enter", "enter", "f"],
            note="Fullscreen visualizer",
            show_viz=True,
        )

        # 4. Filter active — `/` to open the filter, type `pink`. Cursor
        #    should be on browser pane initially, so the filter narrows
        #    artists to Pink Floyd.
        await _capture(
            "filter-active",
            root,
            out_dir,
            keys=["slash", "p", "i", "n", "k"],
            note="`/` filter narrowing the artist pane",
        )

        # 5. Help panel — `?` toggles Textual's HelpPanel listing all
        #    bindings.
        await _capture(
            "help-panel",
            root,
            out_dir,
            keys=["question_mark"],
            note="`?` HelpPanel — full keybindings list",
        )

    print(f"Done. Wrote {len(list(out_dir.glob('*.svg')))} SVG(s) to {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
