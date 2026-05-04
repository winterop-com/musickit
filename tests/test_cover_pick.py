"""cover-pick: helper unit tests (the interactive flow is exercised manually)."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from musickit.cli.cover_pick import _audit_reason, _collect_candidates, _normalise
from musickit.library.models import LibraryAlbum


def test_normalise_resizes_and_reencodes_jpeg() -> None:
    big = Image.new("RGB", (2000, 2000), color=(120, 80, 200))
    buf = io.BytesIO()
    big.save(buf, format="JPEG", quality=90)

    out_bytes, mime, dims = _normalise(buf.getvalue(), max_edge=400)

    assert mime == "image/jpeg"
    assert max(dims) <= 400
    # Output should be a valid JPEG round-trip.
    with Image.open(io.BytesIO(out_bytes)) as round_tripped:
        assert round_tripped.format == "JPEG"


def test_normalise_keeps_alpha_channel_as_png() -> None:
    rgba = Image.new("RGBA", (300, 300), color=(0, 0, 0, 0))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")

    _out_bytes, mime, _dims = _normalise(buf.getvalue(), max_edge=200)
    assert mime == "image/png"


def test_audit_reason_classifies_albums() -> None:
    no_cover = LibraryAlbum(path=Path("/tmp/a"), artist_dir="A", album_dir="X", has_cover=False)
    low_res = LibraryAlbum(
        path=Path("/tmp/b"),
        artist_dir="B",
        album_dir="Y",
        has_cover=True,
        cover_pixels=200 * 200,
    )
    fine = LibraryAlbum(
        path=Path("/tmp/c"),
        artist_dir="C",
        album_dir="Z",
        has_cover=True,
        cover_pixels=1000 * 1000,
    )
    assert _audit_reason(no_cover) == "no cover"
    assert "low-res" in _audit_reason(low_res)
    assert _audit_reason(fine) == "manual pick"


def test_collect_candidates_filters_to_issues_when_requested(tmp_path: Path) -> None:
    """An empty dir produces no candidates; --all on an empty dir also produces none."""
    empty = tmp_path / "lib"
    empty.mkdir()
    assert _collect_candidates(empty, issues_only=True) == []
    assert _collect_candidates(empty, issues_only=False) == []
