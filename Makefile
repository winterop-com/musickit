.PHONY: help install lint check test coverage docs docs-serve docs-build docs-screenshots build build-python dist-collect desktop-sync-frontend desktop-sync-version desktop-tauri desktop-tauri-dev desktop-tauri-build desktop-electron desktop-electron-dev desktop-electron-build desktop-react-tauri desktop-react-tauri-dev desktop-react-tauri-build desktop-react-electron desktop-react-electron-dev desktop-react-electron-build ui-static-sync clean

UV := $(shell command -v uv 2> /dev/null)

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install      Install dependencies"
	@echo "  lint         Run ruff (with --fix) + mypy + pyright — local dev, mutates code"
	@echo "  check        Run ruff (no --fix) + mypy + pyright — CI-safe, never mutates"
	@echo "  test         Run pytest"
	@echo "  coverage     Run pytest with coverage"
	@echo "  docs-serve   Serve documentation locally with live reload"
	@echo "  docs-build   Build static documentation site to ./site"
	@echo "  docs-screenshots  Regenerate the TUI SVG screenshots in docs/screenshots/"
	@echo "  docs         Alias for docs-serve"
	@echo "  build        Build release versions of everything; collect into ./dist"
	@echo "  build-python Build Python wheel + sdist via uv build (-> ./dist)"
	@echo "  dist-collect Copy desktop build artifacts into ./dist for easy access"
	@echo "  desktop-tauri        Alias for desktop-tauri-dev"
	@echo "  desktop-tauri-dev    Run the Tauri desktop app in dev mode (cargo tauri dev)"
	@echo "  desktop-tauri-build  Build the Tauri desktop app .app bundle (release)"
	@echo "  desktop-electron     Alias for desktop-electron-dev"
	@echo "  desktop-electron-dev Run the Electron desktop app in dev mode (npm start)"
	@echo "  desktop-electron-build Build the Electron app .dmg under desktop/electron/dist/"
	@echo "  desktop-react-tauri-dev    Run the Tauri design-v2 prototype (desktop/react/)"
	@echo "  desktop-react-tauri-build  Build the Tauri design-v2 .app bundle"
	@echo "  desktop-react-electron-dev Run the Electron design-v2 prototype (desktop/react/)"
	@echo "  desktop-react-electron-build Build the Electron design-v2 .dmg under desktop/electron-react/dist/"
	@echo "  clean        Remove caches and build artifacts"

install:
	@echo ">>> Installing dependencies"
	@$(UV) sync

lint:
	@echo ">>> Running linter"
	@$(UV) run ruff format .
	@$(UV) run ruff check . --fix
	@echo ">>> Running type checkers"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright

check:
	@echo ">>> Running format check (no mutations)"
	@$(UV) run ruff format --check .
	@echo ">>> Running lint check (no mutations)"
	@$(UV) run ruff check .
	@echo ">>> Running type checkers"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright

test:
	@echo ">>> Running tests"
	@$(UV) run pytest -q

coverage:
	@echo ">>> Running tests with coverage"
	@$(UV) run coverage run -m pytest -q
	@$(UV) run coverage report
	@$(UV) run coverage xml

docs-serve:
	@echo ">>> Serving documentation at http://127.0.0.1:8000"
	@$(UV) run mkdocs serve

docs-build:
	@echo ">>> Building documentation site"
	@$(UV) run mkdocs build

docs-screenshots:
	@echo ">>> Regenerating TUI SVG screenshots"
	@$(UV) run python scripts/gen_screenshots.py

docs: docs-serve

# ---------------------------------------------------------------------------
# Desktop wrappers
#
# `desktop/frontend/` is the shared picker UI (HTML/CSS/JS). Each
# desktop wrapper (`desktop/tauri/`, future `desktop/electron/`) loads
# this same frontend in its native webview. The frontend's CSS palette
# is symlinked from `src/musickit/web/static/_palette.css` so colour
# changes propagate to both web + desktop on save.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Release builds
#
# `make build` produces release versions of every shippable surface,
# then collects them all into ./dist for easy access:
#
#   ./dist/musickit-X.Y.Z-py3-none-any.whl       (Python wheel)
#   ./dist/musickit-X.Y.Z.tar.gz                  (Python sdist)
#   ./dist/MusicKit-Tauri-X.Y.Z-aarch64.dmg       (Tauri DMG)
#   ./dist/MusicKit-Tauri.app                     (Tauri app bundle)
#   ./dist/MusicKit-Electron-X.Y.Z-arm64.dmg      (Electron DMG)
#
# Each sub-target can also run on its own — useful when you only need
# one artifact (e.g. CI publishing the Python wheel without touching
# the desktop apps). Sub-targets still write to their native build
# directories first; `dist-collect` is the single place that copies
# them all into ./dist.
# ---------------------------------------------------------------------------

# Read the current version from pyproject.toml so dist-collect copies
# ONLY the current build's artifacts and ignores stale older versions
# left behind in desktop/electron/dist (electron-builder doesn't prune
# its own output dir between builds, and old DMGs accumulate fast at
# ~95 MB each).
VERSION := $(shell grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')

build: build-python desktop-tauri-build desktop-electron-build dist-collect
	@echo ">>> All release builds complete. Artifacts collected in ./dist (v$(VERSION)):"
	@ls -lh dist/ | tail -n +2

build-python: ui-static-sync
	@echo ">>> Building Python wheel + sdist into ./dist"
	@$(UV) build

ui-static-sync:
	@$(UV) run python scripts/copy_ui_static.py

# Copy desktop artifacts into ./dist alongside the Python wheel + sdist
# so a single directory has everything `make build` produced. Wipes
# stale prior-version desktop artifacts in ./dist so a clean re-run
# leaves only the current version. Safe to run on its own after a
# partial build (skips files that don't exist yet).
dist-collect:
	@echo ">>> Collecting v$(VERSION) artifacts into ./dist"
	@mkdir -p dist
	@# Wipe prior-version desktop artifacts so dist/ only holds the
	@# current build (Python wheel + sdist already overwrote in place
	@# via uv build).
	@find dist -maxdepth 1 \( -name 'MusicKit-Tauri-*' -o -name 'MusicKit-Electron-*' \) -exec rm -rf {} + 2>/dev/null || true
	@# Tauri DMG for current version (post-renamed by scripts/rename_tauri_artifacts.py)
	@cp -f desktop/tauri/src-tauri/target/release/bundle/dmg/MusicKit-Tauri-$(VERSION)-*.dmg dist/ 2>/dev/null || true
	@# Tauri .app — preserve the bundle directory structure verbatim.
	@if [ -d desktop/tauri/src-tauri/target/release/bundle/macos/MusicKit-Tauri.app ]; then \
		cp -R desktop/tauri/src-tauri/target/release/bundle/macos/MusicKit-Tauri.app dist/; \
	fi
	@# Electron DMG for current version only.
	@cp -f desktop/electron/dist/MusicKit-Electron-$(VERSION)-*.dmg dist/ 2>/dev/null || true
	@# Electron .app — produced under mac-arm64/ as MusicKit.app, copy
	@# with the Electron tag in the name to disambiguate from the Tauri
	@# bundle that lives alongside it in dist/.
	@if [ -d desktop/electron/dist/mac-arm64/MusicKit.app ]; then \
		cp -R desktop/electron/dist/mac-arm64/MusicKit.app dist/MusicKit-Electron.app; \
	fi

desktop-sync-frontend:
	@echo ">>> Syncing shared frontend assets into desktop/frontend/"
	@cp src/musickit/web/static/_palette.css   desktop/frontend/_palette.css
	@cp src/musickit/web/static/app.css        desktop/frontend/_app.css
	@cp src/musickit/web/static/favicon.svg    desktop/frontend/favicon.svg
	@cp src/musickit/web/static/visualizer.js  desktop/frontend/js/visualizer.js

desktop-sync-version:
	@$(UV) run python scripts/sync_desktop_versions.py

desktop-tauri: desktop-tauri-dev

desktop-tauri-dev: desktop-sync-frontend
	@echo ">>> Tauri dev — opens window pointed at desktop/frontend/index.html"
	@cd desktop/tauri/src-tauri && cargo tauri dev

desktop-tauri-build: desktop-sync-frontend desktop-sync-version
	@echo ">>> Tauri release build — produces a .app under desktop/tauri/src-tauri/target/release/bundle/"
	@cd desktop/tauri/src-tauri && cargo tauri build
	@# Tauri 2 has no artifactName option, so post-rename the .dmg /
	@# .app so they're distinguishable from the Electron sibling
	@# ('MusicKit_X.Y.Z_arch.dmg' -> 'MusicKit-Tauri-X.Y.Z-arch.dmg').
	@$(UV) run python scripts/rename_tauri_artifacts.py

desktop-electron: desktop-electron-dev

desktop-electron-dev: desktop-sync-frontend
	@echo ">>> Electron dev — opens window pointed at desktop/frontend/index.html"
	@cd desktop/electron && (test -d node_modules || npm install) && npm start

desktop-electron-build: desktop-sync-frontend desktop-sync-version
	@echo ">>> Electron release build — produces a .dmg under desktop/electron/dist/"
	@cd desktop/electron && (test -d node_modules || npm install) && npm run build

# Design-v2 wrappers. These run the in-progress Claude Designer React
# prototype under desktop/react/ as side-by-side apps (separate bundle
# identifiers, separate product names) so the production wrappers stay
# untouched. The dev targets need no install steps for the frontend
# itself — `desktop/react/` is plain HTML + JSX loaded via Babel.
desktop-react-tauri: desktop-react-tauri-dev

desktop-react-tauri-dev:
	@echo ">>> Tauri dev (design v2) — opens window pointed at desktop/react/index.html"
	@cd desktop/tauri-react/src-tauri && cargo tauri dev

desktop-react-tauri-build:
	@echo ">>> Tauri release build (design v2) — produces a .app under desktop/tauri-react/src-tauri/target/release/bundle/"
	@cd desktop/tauri-react/src-tauri && cargo tauri build

desktop-react-electron: desktop-react-electron-dev

desktop-react-electron-dev:
	@echo ">>> Electron dev (design v2) — opens window pointed at desktop/react/index.html"
	@cd desktop/electron-react && (test -d node_modules || npm install) && npm start

desktop-react-electron-build:
	@echo ">>> Electron release build (design v2) — produces a .dmg under desktop/electron-react/dist/"
	@cd desktop/electron-react && (test -d node_modules || npm install) && npm run build

clean:
	@echo ">>> Cleaning up"
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .coverage htmlcov coverage.xml
	@rm -rf .pyright
	@rm -rf dist build *.egg-info
	@rm -rf site

.DEFAULT_GOAL := help
