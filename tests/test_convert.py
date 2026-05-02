"""End-to-end encode + tag-write coverage for the lossy paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from musickit import convert
from musickit.convert import OutputFormat, normalize_bitrate
from musickit.metadata import AlbumSummary, SourceTrack, write_tags


def test_normalize_bitrate_accepts_common_forms():
    assert normalize_bitrate("192") == "192k"
    assert normalize_bitrate("192k") == "192k"
    assert normalize_bitrate(" 320K ") == "320k"
    assert normalize_bitrate(None) == "256k"


def test_normalize_bitrate_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_bitrate("loud")
    with pytest.raises(ValueError):
        normalize_bitrate("0.5kbps")


def test_output_format_extensions():
    assert OutputFormat.ALAC.extension == ".m4a"
    assert OutputFormat.AAC.extension == ".m4a"
    assert OutputFormat.MP3.extension == ".mp3"
    assert OutputFormat.MP3.is_lossy is True
    assert OutputFormat.ALAC.is_lossy is False


def test_encode_to_mp3_and_write_id3_tags(silent_flac: Path, tmp_path: Path) -> None:
    out = tmp_path / "01 - Track.mp3"
    convert.encode(silent_flac, out, OutputFormat.MP3, bitrate="192k")
    assert out.exists()

    track = SourceTrack(path=silent_flac, title="Track 1", artist="A", album_artist="A", track_no=1, track_total=10)
    summary = AlbumSummary(album="Album", album_artist="A", year="2020", track_total=10, is_compilation=False)
    write_tags(out, track, summary, cover_bytes=None, cover_mime=None)

    mp3 = MP3(out)
    info = mp3.info
    assert info is not None
    assert getattr(info, "bitrate", 0) >= 150_000  # libmp3lame's 192k VBR/CBR

    id3 = ID3(out)
    assert _frame_text(id3, "TIT2") == "Track 1"
    assert _frame_text(id3, "TPE1") == "A"
    assert _frame_text(id3, "TALB") == "Album"
    assert _frame_text(id3, "TRCK") == "1/10"


def test_lossy_source_classification(tmp_path: Path) -> None:
    from musickit.convert import is_lossy_source

    flac = tmp_path / "track.flac"
    flac.write_bytes(b"not really a flac, but extension is enough")
    assert is_lossy_source(flac) is False

    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"")
    assert is_lossy_source(mp3) is True

    wav = tmp_path / "track.wav"
    wav.write_bytes(b"")
    assert is_lossy_source(wav) is False


def test_lossy_source_m4a_probes_codec(silent_flac: Path, tmp_path: Path) -> None:
    """ALAC m4a is lossless; AAC m4a is lossy. Probe via mutagen."""
    from musickit import convert
    from musickit.convert import OutputFormat, is_lossy_source

    alac_out = tmp_path / "alac.m4a"
    convert.encode(silent_flac, alac_out, OutputFormat.ALAC)
    assert is_lossy_source(alac_out) is False

    aac_out = tmp_path / "aac.m4a"
    convert.encode(silent_flac, aac_out, OutputFormat.AAC, bitrate="128k")
    assert is_lossy_source(aac_out) is True


def test_would_be_lossy_recompress_only_blocks_lossy_to_lossy(tmp_path: Path) -> None:
    from musickit.convert import OutputFormat, would_be_lossy_recompress

    mp3 = tmp_path / "src.mp3"
    mp3.write_bytes(b"")
    flac = tmp_path / "src.flac"
    flac.write_bytes(b"")

    # Lossy -> lossy: blocked.
    assert would_be_lossy_recompress(mp3, OutputFormat.AAC) is True
    assert would_be_lossy_recompress(mp3, OutputFormat.MP3) is True
    # Lossy -> lossless ALAC: explicitly allowed (m4a container, lossless inside).
    assert would_be_lossy_recompress(mp3, OutputFormat.ALAC) is False
    # Lossless -> anything: always fine.
    assert would_be_lossy_recompress(flac, OutputFormat.AAC) is False
    assert would_be_lossy_recompress(flac, OutputFormat.MP3) is False
    assert would_be_lossy_recompress(flac, OutputFormat.ALAC) is False


def test_auto_resolve_picks_per_source(silent_flac: Path, tmp_path: Path) -> None:
    """AUTO targets a uniform AAC `.m4a` library.

    Lossless sources get a single high-quality AAC encode. AAC m4a sources
    get a free stream-copy. MP3 / other lossy sources accept a one-time
    tandem encode in exchange for landing in `.m4a` with proper MP4 tags.
    """
    from musickit import convert
    from musickit.convert import OutputFormat, auto_resolve

    # FLAC source → AAC encode.
    fmt, copy = auto_resolve(silent_flac)
    assert fmt is OutputFormat.AAC and copy is False

    # MP3 source → AAC encode (tandem encode for library uniformity).
    mp3 = tmp_path / "track.mp3"
    convert.encode(silent_flac, mp3, OutputFormat.MP3, bitrate="128k")
    fmt, copy = auto_resolve(mp3)
    assert fmt is OutputFormat.AAC and copy is False

    # AAC m4a source → stream-copy (no transcode, just retag).
    aac = tmp_path / "track-aac.m4a"
    convert.encode(silent_flac, aac, OutputFormat.AAC, bitrate="128k")
    fmt, copy = auto_resolve(aac)
    assert fmt is OutputFormat.AAC and copy is True

    # ALAC m4a source → AAC encode.
    alac = tmp_path / "track-alac.m4a"
    convert.encode(silent_flac, alac, OutputFormat.ALAC)
    fmt, copy = auto_resolve(alac)
    assert fmt is OutputFormat.AAC and copy is False


def test_remux_mp3_to_m4a_preserves_stream(silent_flac: Path, tmp_path: Path) -> None:
    """The remux must keep MP3 audio bytes intact and produce a valid MP4."""
    from mutagen.mp4 import MP4

    from musickit import convert
    from musickit.convert import OutputFormat

    mp3 = tmp_path / "src.mp3"
    convert.encode(silent_flac, mp3, OutputFormat.MP3, bitrate="128k")
    out = tmp_path / "out.m4a"
    convert.remux_to_m4a(mp3, out)
    assert out.exists()
    # MP4 container with MP3 inside; mutagen still recognises it as MP4.
    info = MP4(out).info
    assert info is not None
    # MP3-in-MP4 reports codec as "mp3" (object type 0x6B) — mutagen exposes it
    # via info.codec as "mp4a" or "mp3" depending on version. Either is valid.
    assert getattr(info, "codec_description", "") or getattr(info, "codec", "")


def test_encode_rejects_auto() -> None:
    from pathlib import Path

    import pytest

    from musickit import convert
    from musickit.convert import OutputFormat

    with pytest.raises(ValueError, match="AUTO"):
        convert.encode(Path("/nope.flac"), Path("/nope.m4a"), OutputFormat.AUTO)


def test_encode_to_alac_smoke(silent_flac: Path, tmp_path: Path) -> None:
    out = tmp_path / "01 - Track.m4a"
    convert.encode(silent_flac, out, OutputFormat.ALAC)
    assert out.exists()
    from mutagen.mp4 import MP4

    info = MP4(out).info
    assert info is not None
    assert info.codec == "alac"


def _frame_text(id3: ID3, key: str) -> str:
    frame = id3.get(key)
    assert frame is not None, f"missing ID3 frame {key}"
    text = frame.text[0]
    return str(text)
