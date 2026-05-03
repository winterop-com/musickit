"""Per-album orchestration: discover → cover → convert → tag → swap."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console

from musickit import convert, naming
from musickit import cover as cover_mod
from musickit.convert import OutputFormat
from musickit.cover import Cover
from musickit.discover import AlbumDir
from musickit.metadata import MusicBrainzIds, SourceTrack, clean_album_title, read_source, summarize_album
from musickit.pipeline.acoustid import _enrich_with_acoustid
from musickit.pipeline.dedupe import _dedupe_duplicate_tracks
from musickit.pipeline.disc import _maybe_apply_filename_disc_track, _maybe_apply_scene_encoded_disc_track
from musickit.pipeline.filenames import _parse_filename_for_va, _track_no_from_filename
from musickit.pipeline.footprint import _input_footprint
from musickit.pipeline.progress import ProgressContext
from musickit.pipeline.report import AlbumReport
from musickit.pipeline.track import _planned_filename, _process_track, _resolve_track_metadata, _ResolvedTrack


def _process_album(
    album_dir: AlbumDir,
    output_root: Path,
    fmt: OutputFormat,
    bitrate: str,
    enrich: bool,
    dry_run: bool,
    console: Console,
    ctx: ProgressContext,
    written_dirs: set[Path],
    allow_lossy_recompress: bool,
    workers: int,
    cover_max_edge: int,
    acoustid_key: str | None,
    overwrite: bool,
    remove_source: bool,
    input_root: Path,
) -> AlbumReport:
    warnings: list[str] = []
    tracks: list[SourceTrack] = []
    for path in album_dir.tracks:
        try:
            track = read_source(path)
            # Scrub scene-domain "artists" (`LanzamientosMp3.es` etc.) — vandalism
            # by rip groups. Treat as missing so downstream signals pick the real
            # artist from filename slugs / per-track artist majority.
            if naming.is_scene_domain_artist(track.album_artist):
                track.album_artist = None
            if naming.is_scene_domain_artist(track.artist):
                track.artist = None
            # Scrub scene-domain album tags too (`www.0dayvinyls.org`) so the
            # dirname-fallback fires instead of leaking the URL into the album
            # name. clean_album_title would otherwise dot-flatten it to
            # `www 0dayvinyls org`, which is worse.
            if naming.is_scene_domain_artist(track.album):
                track.album = None
            # When discover merged disc subfolders, the folder name is the
            # authoritative disc number — overrides whatever the per-track tag says.
            disc_from_folder = album_dir.disc_of(path)
            if disc_from_folder is not None:
                track.disc_no = disc_from_folder
                track.disc_total = album_dir.disc_total
            # Pre-fill artist/title/track_no from a `NN. Artist - Title.mp3`-style
            # filename when the source tags lack them. Without this, downstream
            # passes (compilation detection, scene-encoded DTT track-number
            # detection, summarize_album disc-1 bias) all see Nones and bail
            # — leaving the album bucketed under `Unknown Artist/` with flat
            # track numbers. Two real-world cases this rescues:
            # - 7Os8Os9Os: 100 tagless MP3s named `NN. Artist - Title.mp3`.
            # - Absolute Music: `116-depeche_mode_-_freelove-atm.mp3` with
            #   tags carrying title/artist/album but NO `track` tag.
            if not track.artist or not track.title:
                parsed_artist, parsed_title = _parse_filename_for_va(path)
                if parsed_artist and not track.artist:
                    track.artist = parsed_artist
                if parsed_title and not track.title:
                    track.title = parsed_title
            if track.track_no is None:
                track.track_no = _track_no_from_filename(path)
            tracks.append(track)
        except Exception as exc:
            warnings.append(f"failed to read {path.name}: {exc}")

    # Dedupe source-side duplicates. Some rip groups ship every track twice
    # under different filename conventions (`01. Artist - Title.flac` AND
    # `01 Title.flac`) — same content, same tags. Without dedup we encode
    # both, hit the output-path collision avoider, and end up with `(2)`
    # suffixes. Key on (disc_no, track_no, title-lower, artist-lower); keep
    # the first occurrence (stable sort upstream picks the canonical
    # `NN. Artist - Title.flac` form when both exist).
    tracks = _dedupe_duplicate_tracks(tracks, warnings)

    # AcoustID enrichment for tagless tracks: fingerprint and look up against
    # https://acoustid.org. Only runs when the user supplied an API key AND
    # the track has no usable title/artist after the filename pre-fill —
    # bringing the network into play only when local data has nothing to say.
    if acoustid_key:
        _enrich_with_acoustid(tracks, acoustid_key, workers, console, ctx, warnings)

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
    _maybe_apply_scene_encoded_disc_track(album_dir, tracks)

    summary = summarize_album(tracks)
    # Folder-level VA detection: `VA-Absolute_Music_60`, `Various - Hits 2024`.
    # Only kicks in when we don't already have a clear single-artist signal —
    # an artist majority of 90%+ should win regardless of folder vandalism.
    if not summary.is_compilation and naming.folder_name_implies_va(album_dir.path.name):
        summary.is_compilation = True
    if not summary.album:
        warnings.append("missing album tag — using input folder name")
        cleaned, folder_year = naming.clean_folder_album_name(album_dir.path.name)
        # The folder may itself end in `(Disc 1)` etc. when merge anchored on
        # one disc subfolder — strip that too.
        summary.album = clean_album_title(cleaned)
        if not summary.year and folder_year:
            summary.year = folder_year
    # Hand-curated leading-year prefix in the dir wins over track tags. Real
    # case: `1983. NTWICM! [2018 Reissue]` ships MP3s tagged 2018, but the
    # leading `1983.` is the deliberate canonical date and overrides.
    leading_year = naming.leading_year_from_folder(album_dir.path.name)
    if leading_year and leading_year != summary.year:
        summary.year = leading_year
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

    artist_name = naming.artist_folder(
        summary.album_artist,
        summary.artist_fallback,
        is_compilation=summary.is_compilation,
    )
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

    # No-replace policy: if the album path already exists on disk from a prior
    # run, skip rather than wiping it. Adding new albums to an existing artist
    # folder is a *merge* — siblings stay untouched. To force a replacement,
    # pass `--overwrite`.
    if out_dir.exists() and not overwrite:
        msg = f"album already exists at {out_dir} — skipped (pass --overwrite to replace)"
        warnings.append(msg)
        console.print(f"[yellow]⚠ skipping {artist_name} / {album_name}: already in output[/yellow]")
        written_dirs.add(out_dir)
        return AlbumReport(
            input_dir=album_dir.path,
            output_dir=out_dir,
            artist=artist_name,
            album=album_name,
            track_count=len(tracks),
            cover_source=cover.source if cover else None,
            cover_size=cover_size,
            warnings=warnings,
            error="album already exists",
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

    # `--remove-source`: now that the album has succeeded the swap, free the
    # source dir on disk. Computes the album's input footprint (covers
    # single-dir, wrapped multi-disc, and the special anchored-at-first-disc
    # case) so removing one album doesn't take down siblings.
    if remove_source:
        try:
            input_root_resolved = input_root.resolve()
        except OSError:
            input_root_resolved = input_root
        for footprint in _input_footprint(album_dir):
            try:
                footprint_resolved = footprint.resolve()
            except OSError:
                footprint_resolved = footprint
            try:
                footprint_resolved.relative_to(input_root_resolved)
                inside_input = footprint_resolved != input_root_resolved
            except ValueError:
                inside_input = False
            if not inside_input:
                warnings.append(
                    f"--remove-source: refusing to remove {footprint} (would touch input root or escape it)"
                )
                continue
            try:
                shutil.rmtree(footprint)
                if ctx.verbose:
                    console.print(f"    [dim]removed source: {footprint}[/dim]")
            except OSError as exc:
                warnings.append(f"--remove-source: failed to remove {footprint}: {exc}")

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
