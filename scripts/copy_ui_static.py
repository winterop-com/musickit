"""Copy `desktop/frontend/` → `src/musickit/_ui_static/` so the wheel bundles it.

`musickit ui` discovers the SPA static files via
`importlib.resources.files("musickit") / "_ui_static"`. Both Tauri and
Electron point at the source `desktop/frontend/` directly; this build
step only matters when someone installs musickit from PyPI and wants to
run `musickit ui` without cloning the repo.

Wired into `make build` so every wheel ships with the SPA bundled. The
destination is gitignored — it's a regenerated build artifact, not a
hand-edited source tree.

Idempotent; safe to run multiple times. Wipes the destination first so
a removed source file doesn't linger in the bundle.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "desktop" / "frontend"
DEST = REPO_ROOT / "src" / "musickit" / "_ui_static"


def main() -> None:
    """Run the copy; exit non-zero with a clear message on failure."""
    if not SOURCE.is_dir():
        sys.exit(f"copy_ui_static: source not found: {SOURCE}")
    if not (SOURCE / "index.html").exists():
        sys.exit(f"copy_ui_static: source is missing index.html: {SOURCE}")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(SOURCE, DEST)
    print(f">>> Bundled UI static files {SOURCE.relative_to(REPO_ROOT)} -> {DEST.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
