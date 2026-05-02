.PHONY: help install lint test coverage clean

UV := $(shell command -v uv 2> /dev/null)

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install    Install dependencies"
	@echo "  lint       Run ruff + mypy + pyright"
	@echo "  test       Run pytest"
	@echo "  coverage   Run pytest with coverage"
	@echo "  clean      Remove caches and build artifacts"

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

test:
	@echo ">>> Running tests"
	@$(UV) run pytest -q

coverage:
	@echo ">>> Running tests with coverage"
	@$(UV) run coverage run -m pytest -q
	@$(UV) run coverage report
	@$(UV) run coverage xml

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

.DEFAULT_GOAL := help
