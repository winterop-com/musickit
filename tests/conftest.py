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
