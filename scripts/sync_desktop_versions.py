"""Propagate `pyproject.toml`'s version to the desktop wrappers.

The Python package is the single source of truth for the project's
version. The Tauri bundle (`Cargo.toml` + `tauri.conf.json`) and the
Electron `package.json` need to mirror it so the .app / .dmg artifacts
report a consistent number, and so the Tauri release-build embeds the
right `productVersion` string into Info.plist + the .dmg filename.

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


def update_cargo_toml(path: Path, version: str) -> bool:
    """Rewrite `version = "..."` in the [package] block. Returns True if changed."""
    text = path.read_text(encoding="utf-8")
    # Tauri's Cargo.toml has only one `[package]` block + one `version =`
    # under it, so a simple sed-style substitution is enough.
    pattern = re.compile(r'^version\s*=\s*"[^"]*"\s*$', re.MULTILINE)
    new_line = f'version = "{version}"'
    new_text, count = pattern.subn(new_line, text, count=1)
    if count == 0:
        raise SystemExit(f'no `version = "..."` line in {path}')
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


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
    """CLI entrypoint: sync all desktop versions to pyproject.toml."""
    version = read_pyproject_version()
    print(f">>> Syncing desktop versions to {version}")
    targets: list[tuple[Path, str]] = []
    cargo = REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "Cargo.toml"
    tauri_conf = REPO_ROOT / "desktop" / "tauri" / "src-tauri" / "tauri.conf.json"
    electron_pkg = REPO_ROOT / "desktop" / "electron" / "package.json"
    if update_cargo_toml(cargo, version):
        targets.append((cargo, "updated"))
    else:
        targets.append((cargo, "already in sync"))
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
