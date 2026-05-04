# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Emoji policy

No emojis or decorative Unicode in:
- Commit messages, PR titles, PR descriptions
- Code identifiers, comments, docstrings
- Documentation, README, mkdocs pages
- AI-generated chat output

In those contexts use plain text:
- "[x]" instead of a check mark
- "[ ]" instead of a cross mark
- "Note:" instead of a note icon
- "WARNING:" instead of a warning icon

EXCEPTION — TUI / UI rendering. The TUI is a graphical surface that renders
Unicode block-drawing chars, glyphs, arrows, and status icons as part of its
visual language. Symbols like `▶ ♪ ◉ ☺ ▦ ⚠ ✓ ─ ▸ 📻` ARE allowed inside
user-facing TUI strings (notifications, row labels, status panels, widget
content). The rule above is about source-code and prose hygiene, not about
restricting what the rendered UI can show.

## Project Overview

This repository is a curriculum of hands-on tutorials for the Claude Agent SDK
(Python). Each tutorial is a single, self-contained, runnable script that
showcases one concept or feature of the SDK.

Tutorials live in `examples/` and are numbered `<phase>_<step>_<topic>.py`.
Higher phase numbers build on earlier ones.

## Code Quality Standards

- Python 3.13+, line length 120, type annotations required
- Double quotes, async/await, conventional commits
- After changes, run:
  - `make lint` to format and auto-fix
  - `make check` to verify formatting, lint, and types without mutating
- Both targets must pass before committing.

## Documentation Requirements

- Every Python file: one-line module docstring at the top
- Every public class: one-line docstring
- Every public function/method: one-line docstring
- Use triple quotes `"""docstring"""`
- Keep concise - one line preferred
- Tutorial files MAY include a longer leading comment block explaining the
  pedagogical goal of that tutorial (what the reader is meant to learn)

## Tutorial conventions

- Each tutorial is runnable as `uv run python examples/<name>.py`.
- Each tutorial begins with a comment block stating: what feature it covers,
  what the reader should learn, and the corresponding doc page.
- Tutorials use `examples/_common.py` for shared message-printing helpers so the
  novel code on the page is the SDK feature being taught.
- Default to `permission_mode="acceptEdits"` only when the tutorial actually
  modifies files; prefer read-only tools otherwise so tutorials are safe to run.
- Never write outside the tutorial's own working directory or `/tmp`.

## Dependency Management

Always use `uv`:
```bash
uv add <package>          # Runtime dependency
uv add --dev <package>    # Dev dependency
```

Never manually edit `pyproject.toml` for dependency changes.

## Commit Messages

- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`
- NEVER include "Co-Authored-By: Claude" or any AI attribution
- NEVER use emojis in commit messages
- Keep messages concise and descriptive

## Git Workflow

Ask the user before creating branches or pull requests.
