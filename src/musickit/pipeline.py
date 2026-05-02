"""Orchestrator: per-album discover → cover → convert → tag → report."""

from __future__ import annotations

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from musickit import convert, naming
from musickit import cover as cover_mod
from musickit.convert import DEFAULT_LOSSY_BITRATE, OutputFormat
from musickit.cover import Cover, CoverSource
from musickit.discover import AlbumDir, discover_albums
from musickit.metadata import (
    AlbumSummary,
    MusicBrainzIds,
    SourceTrack,
    clean_album_title,
    read_source,
    summarize_album,
    write_tags,
)


def default_workers() -> int:
    """Worker thread default: half the available cores, minimum 1."""
    cores = os.cpu_count() or 2
    return max(1, cores // 2)


class AlbumReport(BaseModel):
    """Per-album outcome line shown at the end of a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    input_dir: Path
    output_dir: Path | None
    artist: str
    album: str
    track_count: int
    cover_source: CoverSource | None
    cover_size: str
    warnings: list[str]
    error: str | None = None
    input_bytes: int = 0
    output_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def saved_ratio(self) -> float | None:
        """Fraction of input size saved (0.0 to 1.0). None if no output was produced."""
        if self.input_bytes == 0 or self.output_bytes == 0:
            return None
        return 1.0 - (self.output_bytes / self.input_bytes)


class _ProgressContext(BaseModel):
    """Bundle of progress reporting handles passed down into per-album work."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    progress: Progress | None = None
    albums_task: TaskID | None = None
    tracks_task: TaskID | None = None
    verbose: bool = False


def run(
    input_root: Path,
    output_root: Path,
    *,
    fmt: OutputFormat = OutputFormat.AUTO,
    bitrate: str = DEFAULT_LOSSY_BITRATE,
    enrich: bool | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    allow_lossy_recompress: bool = False,
    workers: int | None = None,
    cover_max_edge: int = cover_mod.DEFAULT_MAX_EDGE,
    console: Console | None = None,
) -> list[AlbumReport]:
    """Convert every album under `input_root` into `fmt` under `output_root`.

    `enrich` tri-state: `None` (auto) probes connectivity and enables enrichment
    when MusicBrainz is reachable; `True` forces enrichment regardless (useful
    on flaky networks/proxies that block our TCP probe but allow HTTP); `False`
    disables it entirely.
    Default UI is a two-level rich progress bar (albums + current-album tracks).
    Pass `verbose=True` to swap the bar for one log line per track.
    """
    console = console or Console()
    convert.ensure_ffmpeg()
    worker_count = max(1, workers if workers is not None else default_workers())

    # Tri-state enrich: None = "auto, probe connectivity"; True = "force on,
    # skip probe"; False = "off". Only the auto case calls is_online so a
    # user who really wants enrichment can bypass a flaky TCP-probe path.
    if enrich is None:
        from musickit.enrich._http import is_online

        if is_online():
            enrich = True
        else:
            console.print(
                "[dim]offline — skipping enrichment (use `--enrich` to force, `--no-enrich` to silence)[/dim]"
            )
            enrich = False

    albums = discover_albums(input_root)
    if not albums:
        console.print(f"[yellow]No albums found under {input_root}")
        return []

    reports: list[AlbumReport] = []
    written_dirs: set[Path] = set()
    if verbose:
        ctx = _ProgressContext(verbose=True)
        for album_dir in albums:
            reports.append(
                _process_album(
                    album_dir,
                    output_root,
                    fmt,
                    bitrate,
                    enrich,
                    dry_run,
                    console,
                    ctx,
                    written_dirs,
                    allow_lossy_recompress,
                    worker_count,
                    cover_max_edge,
                )
            )
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            albums_task = progress.add_task("[bold]albums", total=len(albums))
            tracks_task = progress.add_task("tracks", total=1, visible=False)
            ctx = _ProgressContext(progress=progress, albums_task=albums_task, tracks_task=tracks_task, verbose=False)
            for album_dir in albums:
                reports.append(
                    _process_album(
                        album_dir,
                        output_root,
                        fmt,
                        bitrate,
                        enrich,
                        dry_run,
                        console,
                        ctx,
                        written_dirs,
                        allow_lossy_recompress,
                        worker_count,
                        cover_max_edge,
                    )
                )
                progress.advance(albums_task)

    _print_summary(console, reports)
    return reports


def _process_album(
    album_dir: AlbumDir,
    output_root: Path,
    fmt: OutputFormat,
    bitrate: str,
    enrich: bool,
    dry_run: bool,
    console: Console,
    ctx: _ProgressContext,
    written_dirs: set[Path],
    allow_lossy_recompress: bool,
    workers: int,
    cover_max_edge: int,
) -> AlbumReport:
    warnings: list[str] = []
    tracks: list[SourceTrack] = []
    for path in album_dir.tracks:
        try:
            track = read_source(path)
            # When discover merged disc subfolders, the folder name is the
            # authoritative disc number — overrides whatever the per-track tag says.
            disc_from_folder = album_dir.disc_of(path)
            if disc_from_folder is not None:
                track.disc_no = disc_from_folder
                track.disc_total = album_dir.disc_total
            tracks.append(track)
        except Exception as exc:
            warnings.append(f"failed to read {path.name}: {exc}")

    if not tracks:
        return AlbumReport(
            input_dir=album_dir.path,
            output_dir=None,
            artist="?",
            album=album_dir.path.name,
            track_count=0,
            cover_source=None,
            cover_size="-",
            warnings=warnings,
            error="no readable tracks",
        )

    _maybe_apply_filename_disc_track(album_dir, tracks)

    summary = summarize_album(tracks)
    if not summary.album:
        warnings.append("missing album tag — using input folder name")
        cleaned, folder_year = naming.clean_folder_album_name(album_dir.path.name)
        # The folder may itself end in `(Disc 1)` etc. when merge anchored on
        # one disc subfolder — strip that too.
        summary.album = clean_album_title(cleaned)
        if not summary.year and folder_year:
            summary.year = folder_year
    if not summary.year:
        # Last-ditch: try pulling a year out of the input folder name.
        _, folder_year = naming.clean_folder_album_name(album_dir.path.name)
        if folder_year:
            summary.year = folder_year
    if album_dir.disc_total and not summary.disc_total:
        summary.disc_total = album_dir.disc_total
    if not summary.year:
        warnings.append("missing year")

    input_bytes = 0
    for src in album_dir.tracks:
        try:
            input_bytes += src.stat().st_size
        except OSError:
            pass

    artist_name = naming.artist_folder(summary.album_artist, summary.artist_fallback)
    album_name = naming.album_folder(summary.album, summary.year)
    out_dir = output_root / artist_name / album_name

    if ctx.verbose:
        console.print(f"[cyan]→[/cyan] {artist_name} / {album_name} ({len(tracks)} tracks)")

    candidates = cover_mod.collect_candidates(album_dir.path, tracks)
    musicbrainz: MusicBrainzIds | None = None
    if enrich:
        from musickit.enrich import run_enrichment

        if ctx.verbose:
            console.print("    [dim]enriching via online providers…[/dim]")
        enrichment = run_enrichment(summary, tracks)
        candidates.extend(enrichment.extra_covers)
        musicbrainz = enrichment.musicbrainz
        warnings.extend(enrichment.notes)

    cover: Cover | None = None
    cover_size = "no cover"
    remaining = list(candidates)
    while remaining:
        chosen = cover_mod.pick_best(remaining)
        if chosen is None:
            break
        try:
            cover = cover_mod.normalize(chosen, max_edge=cover_max_edge)
            cover_size = f"{cover.width}x{cover.height} ({cover.source.value})"
            if ctx.verbose:
                console.print(f"    [dim]cover: {cover_size} from {cover.label}[/dim]")
            break
        except Exception as exc:
            # Pillow refused this candidate (corrupt bytes, weird format).
            # Drop it and try the next-best.
            warnings.append(f"cover candidate {chosen.label!r} unusable: {exc}")
            remaining = [c for c in remaining if c is not chosen]
            cover = None
    if cover is None:
        warnings.append("no cover art found")
        if ctx.verbose:
            console.print("    [yellow]no cover art found[/yellow]")

    # Collision check runs BEFORE the dry-run early return so `--dry-run`
    # surfaces the same skip behaviour the real run would: two source albums
    # that normalise to the same output path would silently overlap, and the
    # user needs to see that in the plan before kicking off the convert.
    if out_dir in written_dirs:
        # A different input album already wrote (or planned to write) here in
        # this run — refusing to overwrite would lose data, so skip the
        # second one and tell the user.
        msg = f"output dir already produced by another input album: {out_dir}"
        warnings.append(msg)
        console.print(f"[yellow]⚠ skipping {artist_name} / {album_name}: {msg}[/yellow]")
        return AlbumReport(
            input_dir=album_dir.path,
            output_dir=out_dir,
            artist=artist_name,
            album=album_name,
            track_count=len(tracks),
            cover_source=cover.source if cover else None,
            cover_size=cover_size,
            warnings=warnings,
            error="duplicate output dir",
            input_bytes=input_bytes,
        )

    if dry_run:
        # Reserve the path so the next album in this dry-run sees it as taken
        # and produces the same collision warning the real run would.
        written_dirs.add(out_dir)
        console.print(f"[dim]dry-run[/dim] {artist_name} / {album_name} — {len(tracks)} tracks, cover: {cover_size}")
        return AlbumReport(
            input_dir=album_dir.path,
            output_dir=out_dir,
            artist=artist_name,
            album=album_name,
            track_count=len(tracks),
            cover_source=cover.source if cover else None,
            cover_size=cover_size,
            warnings=warnings,
            input_bytes=input_bytes,
        )

    # Reserve the output path *now*, before the (potentially long) encode.
    # If a later album normalises to the same path, it must hit the
    # collision branch above whether or not this album ultimately succeeds —
    # otherwise dry-run and real-run can disagree about what gets written.
    written_dirs.add(out_dir)

    # Encode tracks into a sibling staging dir; only swap into the final
    # `out_dir` once every track has succeeded. This keeps the previous
    # complete output intact if a single ffmpeg/tag write fails halfway
    # through — no half-replaced albums. Leading dot keeps it out of
    # `ls` / Finder while the convert is in flight.
    staging = out_dir.with_name(f".{out_dir.name}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    if ctx.progress is not None and ctx.tracks_task is not None:
        ctx.progress.reset(ctx.tracks_task, total=len(tracks), visible=True, description=f"  {album_name}")

    auto_actions: dict[str, int] = {}  # description → count, for the per-album report
    track_failures: list[str] = []

    # Single planning pass: resolve per-track metadata, codec, planned filename,
    # detect collisions (reserve every FINAL name so disambiguated `(N)` suffix
    # collisions chain correctly), and tally the auto-action labels — all in
    # one walk so we never recompute the same auto_resolve / lossy guard.
    track_plans: list[tuple[SourceTrack, OutputFormat, bool, str, _ResolvedTrack]] = []
    reserved_names: set[str] = set()
    for track in tracks:
        track_fmt = fmt
        copy_only = False
        if fmt is OutputFormat.AUTO:
            track_fmt, copy_only = convert.auto_resolve(track.path)
            label = f"{track.path.suffix.lower()[1:]}→{track_fmt.value}{'(copy)' if copy_only else ''}"
            auto_actions[label] = auto_actions.get(label, 0) + 1
        elif convert.would_be_lossy_recompress(track.path, fmt) and not allow_lossy_recompress:
            track_fmt = OutputFormat.ALAC
            auto_actions["lossy→ALAC fallback"] = auto_actions.get("lossy→ALAC fallback", 0) + 1
        resolved = _resolve_track_metadata(track, summary)
        planned = _planned_filename(track, summary, track_fmt, copy_only, resolved=resolved)
        if planned in reserved_names:
            stem, dot, suffix = planned.rpartition(".")
            n = 2
            while f"{stem} ({n}){dot}{suffix}" in reserved_names:
                n += 1
            disambiguated = f"{stem} ({n}){dot}{suffix}"
            warnings.append(
                f"output filename collision on {planned!r}; renamed to {disambiguated!r} "
                f"(check source tags on {track.path.name})"
            )
            planned = disambiguated
        reserved_names.add(planned)
        track_plans.append((track, track_fmt, copy_only, planned, resolved))

    def encode_one(
        plan: tuple[SourceTrack, OutputFormat, bool, str, _ResolvedTrack],
    ) -> tuple[SourceTrack, str | None, Exception | None]:
        track, track_fmt, copy_only, forced_filename, resolved = plan
        try:
            out_filename = _process_track(
                track,
                summary,
                staging,
                cover,
                musicbrainz,
                fmt=track_fmt,
                bitrate=bitrate,
                copy_only=copy_only,
                forced_filename=forced_filename,
                resolved=resolved,
            )
            return track, out_filename, None
        except Exception as exc:
            return track, None, exc

    # Thread pool: each ffmpeg run is a subprocess so the GIL doesn't block.
    # `as_completed` ordering means the progress bar advances when *any* track
    # finishes, not in submission order — so a slow first track doesn't freeze
    # the bar while later (faster) tracks finish in the background.
    pool_size = min(workers, len(tracks)) or 1
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = {pool.submit(encode_one, plan): plan for plan in track_plans}
        for future in as_completed(futures):
            track, out_filename, err = future.result()
            if err is not None:
                track_failures.append(f"{track.path.name}: {err}")
                warnings.append(f"{track.path.name}: {err}")
                if ctx.verbose:
                    console.print(f"    [red]✗[/red] {track.path.name}: {err}")
            elif ctx.verbose and out_filename:
                src_codec = track.path.suffix.lower()[1:]
                console.print(f"    [green]✓[/green] {out_filename} [dim]({src_codec})[/dim]")
            if ctx.progress is not None and ctx.tracks_task is not None:
                ctx.progress.advance(ctx.tracks_task)

    if auto_actions:
        breakdown = ", ".join(f"{n}× {label}" for label, n in auto_actions.items())
        warnings.append(breakdown)

    if ctx.progress is not None and ctx.tracks_task is not None:
        ctx.progress.update(ctx.tracks_task, visible=False)

    if track_failures:
        # Album failed: drop staging, leave any prior `out_dir` intact, mark error.
        shutil.rmtree(staging, ignore_errors=True)
        error_msg = f"{len(track_failures)} of {len(tracks)} tracks failed"
        console.print(f"[red]✗[/red] {artist_name} / {album_name} — {error_msg}")
        return AlbumReport(
            input_dir=album_dir.path,
            output_dir=out_dir,
            artist=artist_name,
            album=album_name,
            track_count=len(tracks) - len(track_failures),
            cover_source=cover.source if cover else None,
            cover_size=cover_size,
            warnings=warnings,
            error=error_msg,
            input_bytes=input_bytes,
        )

    # Atomic-ish swap: move the existing dir aside, install staging, drop the old.
    backup: Path | None = None
    if out_dir.exists():
        backup = out_dir.with_name(f".{out_dir.name}.backup")
        if backup.exists():
            shutil.rmtree(backup)
        out_dir.rename(backup)
    try:
        staging.rename(out_dir)
    except OSError:
        # Restore the prior album so we don't lose data on a swap failure.
        if backup is not None and not out_dir.exists():
            backup.rename(out_dir)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)

    output_bytes = 0
    for path in out_dir.iterdir():
        if path.is_file():
            try:
                output_bytes += path.stat().st_size
            except OSError:
                pass
    console.print(f"[green]✓[/green] {artist_name} / {album_name} — {len(tracks)} tracks, cover: {cover_size}")

    return AlbumReport(
        input_dir=album_dir.path,
        output_dir=out_dir,
        artist=artist_name,
        album=album_name,
        track_count=len(tracks),
        cover_source=cover.source if cover else None,
        cover_size=cover_size,
        warnings=warnings,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
    )


class _ResolvedTrack(BaseModel):
    """The (title, track_no, artist) triplet derived from tags + filename fallbacks.

    Computed once per track in the planning phase. The resolver never mutates
    the input `SourceTrack`; the worker thread reads from this resolved view
    when it needs the output filename or rewriting tags. Keeps the planning
    loop and the encode loop in lockstep on the same parsing rules.
    """

    title: str
    track_no: int | None
    artist: str | None


def _resolve_track_metadata(track: SourceTrack, summary: AlbumSummary) -> _ResolvedTrack:
    """Compute the user-visible title/track_no/artist for `track`.

    Read-only on the input. The rules:
    - Use tag values when present.
    - When the title is missing or the track's "artist" is a VA placeholder
      (`VA`, `Various`, …), parse the filename for `NN - [VA - ]Artist - Title`.
    - On a compilation where the title still contains ` - ` after a VA
      placeholder artist, split title into per-track artist + title.
    - Fall back to plain `_title_from_filename` / `_track_no_from_filename`
      when nothing else applies.
    """
    title = track.title
    artist = track.artist
    track_no = track.track_no
    artist_is_va_marker = naming.is_various_artists(artist)

    parsed_artist: str | None = None
    parsed_title: str | None = None
    if not title or (summary.is_compilation and (artist_is_va_marker or not artist)):
        parsed_artist, parsed_title = _parse_filename_for_va(track.path)
        if parsed_title and (not title or artist_is_va_marker):
            title = parsed_title
        if parsed_artist and (artist_is_va_marker or not artist):
            artist = parsed_artist

    # Title still has `Artist - Title` shape on a comp track? Split it.
    if summary.is_compilation and naming.is_various_artists(artist) and title and " - " in title:
        head, _, tail = title.partition(" - ")
        if head.strip() and tail.strip():
            artist = head.strip()
            title = tail.strip()

    if not title:
        title = _title_from_filename(track.path)
    if not track_no:
        track_no = _track_no_from_filename(track.path)

    return _ResolvedTrack(title=title, track_no=track_no, artist=artist)


def _planned_filename(
    track: SourceTrack,
    summary: AlbumSummary,
    fmt: OutputFormat,
    copy_only: bool,
    *,
    resolved: _ResolvedTrack | None = None,
) -> str:
    """Compute the destination filename for `track` without touching the disk."""
    res = resolved or _resolve_track_metadata(track, summary)
    output_ext = ".m4a" if (copy_only and fmt is not OutputFormat.MP3) else fmt.extension
    filename_artist = res.artist if summary.is_compilation else None
    return naming.track_filename(
        res.track_no,
        res.title,
        artist=filename_artist,
        disc_no=track.disc_no,
        disc_total=track.disc_total or summary.disc_total,
        extension=output_ext,
    )


def _process_track(
    track: SourceTrack,
    summary: AlbumSummary,
    out_dir: Path,
    cover: Cover | None,
    musicbrainz: MusicBrainzIds | None,
    *,
    fmt: OutputFormat,
    bitrate: str,
    copy_only: bool = False,
    forced_filename: str,
    resolved: _ResolvedTrack,
) -> str:
    """Encode + tag one track. Returns the output filename for verbose logging."""
    # Apply resolver output to the track so write_tags emits the cleaned values
    # — this is the only place we mutate, and it's a worker-local copy in the
    # threadpool by virtue of `track` being the unique per-track object.
    track.title = resolved.title
    track.artist = resolved.artist
    track.track_no = resolved.track_no
    out_path = out_dir / forced_filename

    if copy_only and fmt is OutputFormat.MP3:
        # MP3 → MP3: byte-for-byte copy. Keeps the file as a plain `.mp3` so
        # Finder, Music.app and every player can read its ID3 tags.
        convert.copy_passthrough(track.path, out_path)
    elif copy_only:
        # AAC m4a → fresh m4a remux (audio bytes preserved).
        convert.remux_to_m4a(track.path, out_path)
    else:
        convert.encode(track.path, out_path, fmt, bitrate=bitrate)
    write_tags(
        out_path,
        track,
        summary,
        cover_bytes=cover.data if cover else None,
        cover_mime=cover.mime if cover else None,
        musicbrainz=musicbrainz,
    )
    return forced_filename


_FILENAME_DISC_TRACK_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,3})\s*[.\-_]+\s*(.+)$")


def _maybe_apply_filename_disc_track(album_dir: AlbumDir, tracks: list[SourceTrack]) -> None:
    """Apply `D-NN. Title.flac`-style disc/track encoding to all tracks.

    Triggers only when every track in the album matches the pattern AND at
    least two distinct disc numbers appear (so we don't accidentally treat
    `01-Title.flac` from a single-disc album as a multi-disc layout). Used
    by rips that put the disc + track in the filename rather than a CD
    subfolder (e.g. Zara Larsson 2-CD layout).
    """
    if album_dir.disc_total is not None:
        return  # discover already merged disc subfolders — trust that signal.
    parsed: list[tuple[int, int, str, SourceTrack]] = []
    for track in tracks:
        match = _FILENAME_DISC_TRACK_RE.match(track.path.stem)
        if not match:
            return
        parsed.append((int(match.group(1)), int(match.group(2)), match.group(3).strip(), track))
    discs = {disc_n for disc_n, _, _, _ in parsed}
    if len(discs) < 2:
        return
    disc_total = max(discs)
    for disc_n, track_n, title, track in parsed:
        track.disc_no = disc_n
        track.disc_total = disc_total
        if not track.track_no:
            track.track_no = track_n
        if not track.title:
            track.title = title


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    match = re.match(r"^\s*\d{1,3}\s*[.\-_]+\s*(.+)$", stem)
    return (match.group(1) if match else stem).strip()


def _parse_filename_for_va(path: Path) -> tuple[str | None, str | None]:
    """Parse `NN - [VA - ]Artist - Title` filenames common on VA rips.

    Returns `(artist, title)` if the filename has at least 3 ` - ` segments
    after the track number, otherwise `(None, None)` and the caller falls
    back to `_title_from_filename`. Strips a leading `VA -` segment if present.
    """
    stem = path.stem
    body_match = re.match(r"^\s*\d{1,3}\s*[.\-_]+\s*(.+)$", stem)
    body = body_match.group(1) if body_match else stem
    parts = [p.strip() for p in re.split(r"\s+-\s+", body) if p.strip()]
    if parts and parts[0].lower() in ("va", "various", "various artists"):
        parts = parts[1:]
    if len(parts) < 2:
        return None, None
    artist = " - ".join(parts[:-1])
    title = parts[-1]
    return artist, title


def _track_no_from_filename(path: Path) -> int | None:
    match = re.match(r"^\s*(\d{1,3})", path.stem)
    return int(match.group(1)) if match else None


def _format_bytes(n: int) -> str:
    """Human-readable size (B/KB/MB/GB/TB) with reasonable precision."""
    if n <= 0:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size >= 10 else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _print_summary(console: Console, reports: list[AlbumReport]) -> None:
    table = Table(title="Audio convert — summary", show_lines=False)
    table.add_column("Status")
    table.add_column("Artist")
    table.add_column("Album")
    table.add_column("Tracks", justify="right")
    table.add_column("Cover")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Saved", justify="right")
    table.add_column("Notes")

    total_input = 0
    total_output = 0
    for r in reports:
        status = "[green]ok[/green]" if r.ok else "[red]fail[/red]"
        notes = "; ".join(r.warnings) or ("[red]" + r.error + "[/red]" if r.error else "")
        saved = f"{r.saved_ratio * 100:.0f}%" if r.saved_ratio is not None else "—"
        table.add_row(
            status,
            r.artist,
            r.album,
            str(r.track_count),
            r.cover_size,
            _format_bytes(r.input_bytes),
            _format_bytes(r.output_bytes),
            saved,
            notes,
        )
        total_input += r.input_bytes
        total_output += r.output_bytes

    if reports:
        total_saved = (
            f"{(1.0 - total_output / total_input) * 100:.0f}%" if total_input > 0 and total_output > 0 else "—"
        )
        table.add_section()
        table.add_row(
            "[bold]total[/bold]",
            "",
            "",
            str(sum(r.track_count for r in reports)),
            "",
            f"[bold]{_format_bytes(total_input)}[/bold]",
            f"[bold]{_format_bytes(total_output)}[/bold]",
            f"[bold]{total_saved}[/bold]",
            "",
        )

    console.print(table)
