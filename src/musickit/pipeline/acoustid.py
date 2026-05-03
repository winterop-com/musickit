"""AcoustID enrichment for tracks that arrive without title/artist tags."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from rich.console import Console

from musickit.metadata import SourceTrack
from musickit.pipeline.progress import ProgressContext


def _enrich_with_acoustid(
    tracks: list[SourceTrack],
    api_key: str,
    workers: int,
    console: Console,
    ctx: ProgressContext,
    warnings: list[str],
) -> None:
    """Fingerprint + AcoustID lookup for tracks that still lack title/artist.

    Mutates `tracks` in place: fills `track.title` / `track.artist` when a
    confident match comes back. Failures are recorded as warnings; the
    convert continues with whatever metadata it had.
    """
    candidates = [t for t in tracks if not t.title or not t.artist]
    if not candidates:
        return

    from musickit.enrich.acoustid import AcoustIdProvider, FingerprintMissingError, fpcalc_available

    if not fpcalc_available():
        warnings.append("acoustid: `fpcalc` not on PATH — install chromaprint and rerun")
        return

    provider = AcoustIdProvider(api_key)

    def lookup_one(track: SourceTrack) -> tuple[SourceTrack, str | None]:
        try:
            match = provider.lookup(track.path)
        except FingerprintMissingError as exc:
            return track, str(exc)
        except Exception as exc:  # network blip, malformed JSON, etc. — non-fatal
            return track, f"acoustid: {exc}"
        if match is None:
            return track, None
        if match.title and not track.title:
            track.title = match.title
        if match.artist and not track.artist:
            track.artist = match.artist
        return track, None

    pool_size = min(workers, len(candidates)) or 1
    if ctx.verbose:
        console.print(f"    [dim]acoustid: looking up {len(candidates)} tagless track(s)…[/dim]")
    matched = 0
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for track, err in pool.map(lookup_one, candidates):
            if err:
                warnings.append(err)
            elif track.title and track.artist:
                matched += 1
    if matched:
        warnings.append(f"acoustid: matched {matched}/{len(candidates)} tagless track(s)")
