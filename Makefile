.PHONY: help install lint check test coverage docs docs-serve docs-build docs-screenshots desktop-sync-frontend desktop-tauri desktop-tauri-dev desktop-tauri-build desktop-electron desktop-electron-dev desktop-electron-build clean

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
	@echo "  desktop-tauri        Alias for desktop-tauri-dev"
	@echo "  desktop-tauri-dev    Run the Tauri desktop app in dev mode (cargo tauri dev)"
	@echo "  desktop-tauri-build  Build the Tauri desktop app .app bundle (release)"
	@echo "  desktop-electron     Alias for desktop-electron-dev"
	@echo "  desktop-electron-dev Run the Electron desktop app in dev mode (npm start)"
	@echo "  desktop-electron-build Build the Electron app .dmg under desktop/electron/dist/"
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

desktop-sync-frontend:
	@echo ">>> Syncing shared frontend assets into desktop/frontend/"
	@cp src/musickit/web/static/_palette.css desktop/frontend/_palette.css
	@cp src/musickit/web/static/app.css      desktop/frontend/_app.css
	@cp src/musickit/web/static/favicon.svg  desktop/frontend/favicon.svg

desktop-tauri: desktop-tauri-dev

desktop-tauri-dev: desktop-sync-frontend
	@echo ">>> Tauri dev — opens window pointed at desktop/frontend/index.html"
	@cd desktop/tauri/src-tauri && cargo tauri dev

desktop-tauri-build: desktop-sync-frontend
	@echo ">>> Tauri release build — produces a .app under desktop/tauri/src-tauri/target/release/bundle/"
	@cd desktop/tauri/src-tauri && cargo tauri build

desktop-electron: desktop-electron-dev

desktop-electron-dev: desktop-sync-frontend
	@echo ">>> Electron dev — opens window pointed at desktop/frontend/index.html"
	@cd desktop/electron && (test -d node_modules || npm install) && npm start

desktop-electron-build: desktop-sync-frontend
	@echo ">>> Electron release build — produces a .dmg under desktop/electron/dist/"
	@cd desktop/electron && (test -d node_modules || npm install) && npm run build

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
