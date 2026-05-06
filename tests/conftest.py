"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def silent_flac_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a tiny silent FLAC once per session for tag round-trip tests."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("flac") / "silent.flac"
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
            "flac",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture
def silent_flac(silent_flac_template: Path, tmp_path: Path) -> Path:
    """A fresh, mutable copy of the silent FLAC for one test."""
    dst = tmp_path / "silent.flac"
    shutil.copy2(silent_flac_template, dst)
    return dst


@pytest.fixture(scope="session")
def silent_m4a(silent_flac_template: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped silent .m4a converted from the FLAC template.

    Module-scope was the previous shape, but multiple test files calling
    `convert.to_alac` in the same session triggered a libav segfault on
    the second container open. Session-scope means one conversion total
    per pytest run, regardless of how many files use it.
    """
    from musickit import convert as convert_mod

    out = tmp_path_factory.mktemp("silent_m4a") / "silent.m4a"
    convert_mod.to_alac(silent_flac_template, out)
    return out


def make_silent_flac(dst: Path, *, duration: float = 0.2) -> Path:
    """Encode a silent FLAC of `duration` seconds at `dst`. Used for tests
    that need distinct file sizes (e.g. dedup logic that gates on size).
    """
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
            str(duration),
            "-c:a",
            "flac",
            str(dst),
        ],
        check=True,
    )
    return dst
