"""stream + getCoverArt — Range support, mime types, sidecar + embedded covers."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.ids import album_id, track_id


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def _client_with_index(tmp_path: Path, albums: list[LibraryAlbum]) -> TestClient:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    app.state.cache._reindex(LibraryIndex(root=tmp_path, albums=albums))  # noqa: SLF001
    return TestClient(app)


def _make_silent_m4a(dst: Path, *, duration_s: float = 0.5) -> None:
    """Encode a silent AAC m4a directly via ffmpeg — no convert.encode() round-trip."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
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
            str(duration_s),
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(dst),
        ],
        check=True,
    )


def _real_album_with_one_track(tmp_path: Path, *, with_sidecar_cover: bool = False) -> LibraryAlbum:
    """Encode a 0.5s silent m4a so we have actual bytes to stream."""
    artist = "TestArtist"
    album = "TestAlbum"
    album_dir = tmp_path / artist / album
    album_dir.mkdir(parents=True)
    track_path = album_dir / "01 - Silent.m4a"
    _make_silent_m4a(track_path, duration_s=0.5)
    if with_sidecar_cover:
        # 100x100 red square — small enough to assert byte-exact below.
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        cover_buf = io.BytesIO()
        img.save(cover_buf, format="JPEG")
        (album_dir / "cover.jpg").write_bytes(cover_buf.getvalue())
    track = LibraryTrack(path=track_path, title="Silent", artist=artist, album=album, track_no=1, duration_s=0.5)
    return LibraryAlbum(
        path=album_dir,
        artist_dir=artist,
        album_dir=album,
        tag_album=album,
        track_count=1,
        has_cover=with_sidecar_cover,
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


def test_stream_returns_audio_bytes_with_correct_mime(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    response = client.get("/rest/stream", params=_params(id=track_id(track)))
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mp4"
    # The body should match the file bytes exactly.
    assert response.content == track.path.read_bytes()


def test_stream_advertises_range_support(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/stream", params=_params(id=track_id(album.tracks[0])))
    assert response.headers.get("accept-ranges") == "bytes"


def test_stream_honours_range_header(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    full = track.path.read_bytes()
    end = min(99, len(full) - 1)
    response = client.get(
        "/rest/stream",
        params=_params(id=track_id(track)),
        headers={"Range": f"bytes=0-{end}"},
    )
    assert response.status_code == 206
    assert response.content == full[: end + 1]
    assert response.headers["content-range"] == f"bytes 0-{end}/{len(full)}"


def test_stream_unknown_id_returns_70(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    body = client.get("/rest/stream", params=_params(id="tr_doesnotexist")).json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 70


# ---------------------------------------------------------------------------
# getCoverArt
# ---------------------------------------------------------------------------


def test_get_cover_art_returns_sidecar_jpeg(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path, with_sidecar_cover=True)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/getCoverArt", params=_params(id=album_id(album)))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    # Re-decode and check dimensions match the 100x100 we wrote.
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == (100, 100)


def test_get_cover_art_resizes_when_size_param_given(tmp_path: Path) -> None:
    album = _real_album_with_one_track(tmp_path, with_sidecar_cover=True)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/getCoverArt", params=_params(id=album_id(album), size=50))
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as img:
        # `thumbnail` preserves aspect ratio — for a square that's 50x50.
        assert max(img.size) <= 50


def test_get_cover_art_no_cover_returns_svg_placeholder(tmp_path: Path) -> None:
    """Albums without embedded artwork get a server-side SVG placeholder.

    Subsonic clients vary widely in how they handle a 404 from getCoverArt —
    Feishin shows the browser's broken-image marker, play:Sub leaves an
    empty box. Returning a real image with HTTP 200 (a small ♪ glyph)
    lets every client render something sensible. Navidrome takes the same
    approach.
    """
    album = _real_album_with_one_track(tmp_path, with_sidecar_cover=False)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/getCoverArt", params=_params(id=album_id(album)))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in response.content


def test_get_cover_art_unknown_id_returns_svg_placeholder(tmp_path: Path) -> None:
    """Same placeholder is returned when the id doesn't resolve at all."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/getCoverArt", params=_params(id="al_doesnotexist"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


def test_get_cover_art_via_track_id_resolves_to_album(tmp_path: Path) -> None:
    """Subsonic clients sometimes pass a track ID — that should still pull the album cover."""
    album = _real_album_with_one_track(tmp_path, with_sidecar_cover=True)
    client = _client_with_index(tmp_path, [album])
    response = client.get("/rest/getCoverArt", params=_params(id=track_id(album.tracks[0])))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


# ---------------------------------------------------------------------------
# stream — transcoding
# ---------------------------------------------------------------------------


def test_stream_format_raw_returns_original_bytes(tmp_path: Path) -> None:
    """`format=raw` is the explicit no-transcode opt-out; original bytes + Range stay."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    response = client.get("/rest/stream", params=_params(id=track_id(track), format="raw"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mp4"
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.content == track.path.read_bytes()


def test_stream_format_mp3_transcodes(tmp_path: Path) -> None:
    """`format=mp3` against an m4a source triggers an ffmpeg transcode."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    response = client.get("/rest/stream", params=_params(id=track_id(track), format="mp3"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    # ffmpeg writes an ID3v2 header (`ID3`, 49 44 33) before the MPEG frames;
    # an untagged stream would start with the frame sync 0xFF. Either is valid.
    assert len(response.content) > 0
    assert response.content[:3] == b"ID3" or response.content[0] == 0xFF


def test_stream_max_bit_rate_triggers_transcode(tmp_path: Path) -> None:
    """`maxBitRate=128` (no `format`) also triggers an MP3 transcode per Subsonic spec."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    response = client.get("/rest/stream", params=_params(id=track_id(track), maxBitRate=128))
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 0


def test_stream_max_bit_rate_zero_means_no_transcode(tmp_path: Path) -> None:
    """`maxBitRate=0` is the spec's 'unlimited' value — no transcode."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    response = client.get("/rest/stream", params=_params(id=track_id(track), maxBitRate=0))
    assert response.headers["content-type"] == "audio/mp4"
    assert response.content == track.path.read_bytes()


def test_stream_time_offset_passes_to_ffmpeg(tmp_path: Path) -> None:
    """`timeOffset` (OpenSubsonic transcodeOffset) skips the first N seconds of the transcode."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]

    # Full transcode for reference.
    full = client.get("/rest/stream", params=_params(id=track_id(track), format="mp3")).content

    # 10s offset on a 0.5s track produces a much shorter (often empty-ish) MP3
    # — we just assert the response succeeds and is shorter than the full one.
    offset = client.get(
        "/rest/stream",
        params=_params(id=track_id(track), format="mp3", timeOffset=10),
    )
    assert offset.status_code == 200
    assert offset.headers["content-type"] == "audio/mpeg"
    assert len(offset.content) < len(full)


def test_stream_time_offset_ignored_for_raw(tmp_path: Path) -> None:
    """timeOffset is only meaningful for transcoded streams; raw uses Range."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]

    # format=raw + timeOffset → still raw, full bytes, Range available.
    response = client.get(
        "/rest/stream",
        params=_params(id=track_id(track), format="raw", timeOffset=10),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mp4"
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.content == track.path.read_bytes()


def test_download_never_transcodes(tmp_path: Path) -> None:
    """`download` ignores format/maxBitRate per spec — always raw."""
    album = _real_album_with_one_track(tmp_path)
    client = _client_with_index(tmp_path, [album])
    track = album.tracks[0]
    # Even when format=mp3 is passed, download must give the original.
    response = client.get(
        "/rest/download",
        params=_params(id=track_id(track), format="mp3", maxBitRate=128),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mp4"
    assert response.content == track.path.read_bytes()
