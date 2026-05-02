"""AcoustID acoustic-fingerprint lookup (https://acoustid.org).

Generates a Chromaprint fingerprint via the `fpcalc` binary and looks it up
against AcoustID's database, which returns a MusicBrainz recording MBID plus
the recording's title and artist. Lets us tag-derive title/artist on rips
that have NO embedded tags at all (the `7Os8Os9Os`-style 100-tagless-MP3s
case where the only metadata is in filenames).

Requirements at runtime:
- `fpcalc` on `$PATH` (brew install chromaprint, apt-get install libchromaprint-tools).
- An AcoustID API key — free, register at https://acoustid.org/api-key.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from musickit.enrich._http import get_client, throttled_get

log = logging.getLogger(__name__)

ACOUSTID_BASE = "https://api.acoustid.org/v2"
ACOUSTID_HOST_KEY = "api.acoustid.org"
FPCALC_BIN = "fpcalc"
DEFAULT_MIN_SCORE = 0.85  # confidence below this is treated as no match


class FingerprintMissingError(RuntimeError):
    """Raised when `fpcalc` isn't on `$PATH`."""


class FingerprintResult(BaseModel):
    """Output of `fpcalc -json`: the audio fingerprint + duration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    fingerprint: str
    duration: float


class AcoustIdMatch(BaseModel):
    """A single AcoustID lookup hit: best-confidence recording."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    score: float
    recording_id: str | None = None
    title: str | None = None
    artist: str | None = None


def fpcalc_available() -> bool:
    """True when the `fpcalc` binary is reachable via `$PATH`."""
    return shutil.which(FPCALC_BIN) is not None


def fingerprint(path: Path) -> FingerprintResult | None:
    """Run `fpcalc -json <path>` and parse the result.

    Returns None on any failure (missing binary, decode error, unsupported
    container). The caller is expected to fall back gracefully — fingerprinting
    is best-effort enrichment, never required for the convert to proceed.
    """
    if not fpcalc_available():
        raise FingerprintMissingError("fpcalc not on PATH (install with `brew install chromaprint` on macOS)")
    try:
        result = subprocess.run(
            [FPCALC_BIN, "-json", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.debug("fpcalc timed out on %s", path)
        return None
    if result.returncode != 0:
        log.debug("fpcalc failed on %s: %s", path, result.stderr.strip())
        return None
    try:
        data = json.loads(result.stdout)
        return FingerprintResult(fingerprint=str(data["fingerprint"]), duration=float(data["duration"]))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.debug("fpcalc output parse failed on %s: %s", path, exc)
        return None


class AcoustIdProvider:
    """Fingerprint a track and resolve title/artist via AcoustID."""

    name = "acoustid"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        if not api_key:
            raise ValueError("AcoustID requires an api_key (free at https://acoustid.org/api-key)")
        self._api_key = api_key
        self._client = client
        self._owns_client = client is None
        self._min_score = min_score

    def lookup(self, path: Path) -> AcoustIdMatch | None:
        """Fingerprint `path` and return the best AcoustID match, or None."""
        fp = fingerprint(path)
        if fp is None:
            return None
        client = self._client or get_client()
        try:
            try:
                response = throttled_get(
                    client,
                    f"{ACOUSTID_BASE}/lookup",
                    host_key=ACOUSTID_HOST_KEY,
                    params={
                        "client": self._api_key,
                        "duration": str(int(fp.duration)),
                        "fingerprint": fp.fingerprint,
                        "meta": "recordings",
                    },
                )
            except httpx.HTTPError as exc:
                log.debug("AcoustID lookup failed for %s: %s", path, exc)
                return None
            if response.status_code != 200:
                log.debug("AcoustID HTTP %s for %s", response.status_code, path)
                return None
            return self._parse_best_match(response.json())
        finally:
            if self._owns_client:
                client.close()

    def _parse_best_match(self, payload: dict[str, object]) -> AcoustIdMatch | None:
        if payload.get("status") != "ok":
            return None
        results = payload.get("results") or []
        if not isinstance(results, list) or not results:
            return None
        # Highest-scoring result that has at least one recording attached.
        for raw in sorted(results, key=lambda r: float(r.get("score", 0)) if isinstance(r, dict) else 0, reverse=True):
            if not isinstance(raw, dict):
                continue
            score = float(raw.get("score", 0))
            if score < self._min_score:
                return None  # everything below threshold; nothing to gain by checking lower hits
            recordings = raw.get("recordings") or []
            if not isinstance(recordings, list) or not recordings:
                continue
            recording = recordings[0]
            if not isinstance(recording, dict):
                continue
            artists = recording.get("artists") or []
            artist_name: str | None = None
            if isinstance(artists, list) and artists and isinstance(artists[0], dict):
                artist_name = str(artists[0].get("name") or "") or None
            return AcoustIdMatch(
                score=score,
                recording_id=str(recording.get("id") or "") or None,
                title=str(recording.get("title") or "") or None,
                artist=artist_name,
            )
        return None
