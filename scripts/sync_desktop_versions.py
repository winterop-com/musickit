"""Propagate `pyproject.toml`'s version to the desktop wrappers.

The Python package is the single source of truth for the project's
version. The user-visible bundle metadata (Tauri `tauri.conf.json` and
Electron `package.json`) needs to mirror it so the .app / .dmg
artifacts report a consistent number — those values flow into
Info.plist's `CFBundleShortVersionString`, the DMG filename
(`MusicKit-Tauri-X.Y.Z-…dmg` / `MusicKit-Electron-X.Y.Z-…dmg`), and
the macOS About window.

We deliberately do NOT sync `desktop/tauri/src-tauri/Cargo.toml`'s
`[package].version`. That field is internal Cargo metadata; nothing
user-facing reads it. Bumping it on every release caused
`Cargo.lock`'s `musickit-desktop` entry to drift by one version
because CI never runs `cargo build` to refresh the lock — every
Python release left a stale lock entry that needed a follow-up
chore PR. Pinning the crate at a stable internal version (currently
0.1.0) decouples the lock from the project version and removes the
drift entirely.

Run via `make desktop-sync-version` (auto-invoked by the
desktop-{tauri,electron}-build targets). Idempotent — safe to run
multiple times; only writes files where the version has actually
changed.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_pyproject_version() -> str:
    """Pull the `[project].version` string out of pyproject.toml."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    version = data["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[\w.+-]*)?", version):
        raise SystemExit(f"unexpected version shape: {version!r}")
    return version


def update_json_version(path: Path, version: str) -> bool:
    """Update top-level `version` field in a JSON file. Returns True if changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") == version:
        return False
    data["version"] = version
    # Preserve indent style — tauri.conf.json + package.json both use 2 spaces.
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    """CLI entrypoint: sync user-visible desktop versions to pyproject.toml."""
    version = read_pyproject_version()
    print(f">>> Syncing desktop versions to {version}")
    targets: list[tuple[Path, str]] = []
    tauri_conf = REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "tauri.conf.json"
    electron_pkg = REPO_ROOT / "desktop" / "electron" / "package.json"
    if update_json_version(tauri_conf, version):
        targets.append((tauri_conf, "updated"))
    else:
        targets.append((tauri_conf, "already in sync"))
    if update_json_version(electron_pkg, version):
        targets.append((electron_pkg, "updated"))
    else:
        targets.append((electron_pkg, "already in sync"))
    for path, status in targets:
        print(f"    {status:18} {path.relative_to(REPO_ROOT)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
