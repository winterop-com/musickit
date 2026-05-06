"""`.lrc` sidecar IO — read / write next to the audio file."""

from __future__ import annotations

from pathlib import Path

import pytest

from musickit.lyrics import read_sidecar, sidecar_path, write_sidecar


def test_sidecar_path_preserves_audio_suffix(tmp_path: Path) -> None:
    flac = tmp_path / "song.flac"
    assert sidecar_path(flac) == tmp_path / "song.flac.lrc"


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert read_sidecar(tmp_path / "nope.flac") is None


def test_round_trip_utf8(tmp_path: Path) -> None:
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"")  # placeholder so the parent path is real
    body = "[00:01.00]Beyoncé\n[00:02.00]Mañana"
    write_sidecar(audio, body)
    assert read_sidecar(audio) == body


def test_empty_text_skipped(tmp_path: Path) -> None:
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"")
    write_sidecar(audio, "")
    assert read_sidecar(audio) is None


def test_replace_existing(tmp_path: Path) -> None:
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"")
    write_sidecar(audio, "first")
    write_sidecar(audio, "second")
    assert read_sidecar(audio) == "second"


def test_write_failure_cleans_up_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Atomic-write failure must not leave a `.tmp` sibling lying around."""
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"")

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("musickit.lyrics.sidecar.os.replace", _fail)
    with pytest.raises(OSError):
        write_sidecar(audio, "hello")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
