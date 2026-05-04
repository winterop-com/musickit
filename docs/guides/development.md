# Development

## Prerequisites

- Python 3.13
- `uv` (https://docs.astral.sh/uv/) for dependency management
- `ffmpeg` and `ffprobe` on `$PATH` (for the convert pipeline + transcoding)

## Setup

```bash
git clone https://github.com/winterop-com/musickit
cd musickit
uv sync
```

`uv sync` creates the `.venv`, installs runtime deps (FastAPI, PyAV, sounddevice, mutagen, Pillow, pyatv, zeroconf, watchdog, ...) and dev deps (ruff, mypy, pyright, pytest, mkdocs).

## Common commands

```bash
make lint        # ruff format + check + mypy + pyright
make test        # pytest -q
make coverage    # pytest with coverage report
make docs-serve  # mkdocs live-reload at http://127.0.0.1:8000
make docs-build  # build static site to ./site
```

Both `make lint` and `make test` must pass before commit.

## Project layout

```
src/musickit/
  __init__.py        __main__.py
  cli/               typer entry; one file per subcommand
    __init__.py      app = typer.Typer(...) + side-effect imports
    convert.py       cover.py     cover_pick.py    inspect.py
    library.py       retag.py     serve.py         tui.py
    _scan.py         shared scan-progress wrapper
  convert.py         ffmpeg encode / remux / copy
  cover.py           cover-source candidates + pick_best + normalise
  discover.py        walk input → list[AlbumDir] (with multi-disc merge)
  library/           Artist→Album→Track index of the converted output
    __init__.py      models.py    scan.py      audit.py    fix.py
  metadata/          tag read / write
    __init__.py      models.py    album.py     read.py     write.py    overrides.py
  naming.py          filesystem-safe folder + filename builders
  pipeline/          orchestrator — discover → cover → convert → tag → swap
    __init__.py      run.py       album.py     track.py    report.py   progress.py
    filenames.py     disc.py      dedupe.py    footprint.py acoustid.py
  radio.py           curated radio-station list (NRK defaults + user TOML merge)
  serve/             Subsonic-compatible HTTP server
    __init__.py      app.py       auth.py      config.py
    ids.py           index.py     payloads.py  covers.py    xml.py
    discovery.py     watcher.py
    endpoints/       __init__.py  system.py    browsing.py  media.py
                     search.py    scan.py      extras.py
  tui/               Textual TUI + audio engine
    __init__.py      app.py       widgets.py
    player.py        audio_engine.py  audio_proto.py  audio_io.py
    advance.py       commands.py  formatters.py state.py    types.py
    subsonic_client.py            airplay.py   airplay_picker.py
    discovery.py
  enrich/            __init__.py  _http.py     musicbrainz.py
                     coverart.py  musichoarders.py  acoustid.py
tests/               pytest suite
docs/                this site
pyproject.toml       Makefile     mkdocs.yml
input/.gitkeep       output/.gitkeep
```

The major modules went through a 7-wave refactor early on — every package above (`cli/`, `library/`, `metadata/`, `pipeline/`, `serve/`, `tui/`) used to be a single file. Each split is committed separately; check `git log --oneline | grep "Refactor"` for the history.

## Code style

- **Python 3.13**, `from __future__ import annotations` everywhere, explicit type annotations.
- **Line length 120**. ruff-formatted.
- **Docstrings**: one-line module docstring at the top of every file. One-line docstring on every public class / function / method. Triple quotes always.
- **Pydantic for data classes** — `BaseModel` (or `dataclass(frozen=True)` for immutables that don't need pydantic features).
- **Async/await** where the framework demands it (FastAPI endpoints, Textual lifecycle hooks, pyatv); synchronous everywhere else.
- **No emojis** in code, comments, commit messages, PR titles, docs. Plain text only — `[x]` not `✓`, `WARNING:` not warning glyph. Codified in `CLAUDE.md`.

Ruff config (in `pyproject.toml`): `E/W/F/I/D`, google docstrings, `py313`, `line-length 120`.

Mypy: strict-ish. Pyright: `strict` with the same `report*` softeners ruff has.

## Testing

```bash
make test                                     # full suite, quiet
uv run pytest -xvs tests/test_specific.py    # one file, verbose
uv run pytest -k "test_name_substring"       # match by name
```

Test patterns:

- **Convert pipeline**: `silent_flac_template` fixture in `conftest.py` produces a 0.2s silent FLAC via ffmpeg; tests build synthetic libraries from copies.
- **Library / metadata**: `_make_track` helper writes mutagen tags onto a copy of the silent flac.
- **Serve API**: synthetic in-memory `LibraryIndex` injected via `app.state.cache._reindex(...)` — no disk walk. FastAPI `TestClient` for HTTP round-trips.
- **TUI audio engine**: `AudioEngine` is the unit under test; `_FakeOutputStream` replaces `sounddevice.OutputStream` with a thread-driven fake that exercises the callback without opening a real device. `AudioPlayer` (the public RPC client that spawns the subprocess) gets a smoke test for volume; full-engine tests run the engine in-process so monkey-patching works.
- **AirPlay**: pyatv `scan` / `connect` mocked via `unittest.mock.AsyncMock`.
- **mDNS**: real Zeroconf register/unregister smoke (skips on environments without IPv4 multicast); listener filtering tested with mocked `ServiceInfo`.

Coverage runs via `make coverage`; CI thresholds set in `pyproject.toml`.

## Adding a new subcommand

Top-level commands live on the root `app` Typer instance; library-related ones live on the `library_app` subapp (so the user types `musickit library <verb>`).

1. Create `src/musickit/cli/<name>.py`:
   ```python
   # Top-level (e.g. another sibling of convert / inspect / tui / serve):
   from musickit.cli import app

   @app.command(name="my-cmd")
   def my_cmd(...):
       """One-line docstring."""
       ...

   # Library subcommand (e.g. another sibling of cover / retag / cover-pick):
   from musickit.cli.library import library_app

   @library_app.command(name="my-cmd")
   def my_cmd(...):
       """One-line docstring."""
       ...
   ```
2. Add a side-effect import in `cli/__init__.py`. `library` MUST be imported before any module that registers on `library_app`:
   ```python
   from musickit.cli import library as _library_cmd  # noqa: E402
   from musickit.cli import my_cmd as _my_cmd_cmd    # noqa: E402
   _ = (..., _my_cmd_cmd)
   ```

Typer handles the rest — `musickit my-cmd --help` (or `musickit library my-cmd --help`) Just Works.

## Adding a new Subsonic endpoint

1. Pick the right router in `src/musickit/serve/endpoints/` (browsing, search, media, scan, system, extras).
2. Add the endpoint:
   ```python
   @router.api_route("/myEndpoint", methods=["GET", "POST", "HEAD"])
   @router.api_route("/myEndpoint.view", methods=["GET", "POST", "HEAD"])
   async def my_endpoint(request: Request, id: str = Query(...)) -> dict:
       cache = _get_cache(request)
       ...
       return envelope("payloadKey", payload)
   ```
3. Add a test in `tests/test_serve_<group>.py`:
   ```python
   def test_my_endpoint(tmp_path: Path) -> None:
       client = _client_with_index(tmp_path, [...])
       body = client.get("/rest/myEndpoint", params=_params(id="...")).json()
       assert body["subsonic-response"]["status"] == "ok"
   ```

The `_params(...)` helper auto-includes `f=json`. The middleware re-serialises to XML when `f != "json"` — endpoints just return dicts.

## Commit style

Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`. Don't include AI attribution in commit messages.

The repo's commit history is reasonably granular — each "Refactor X (N lines)" commit, each Subsonic-API phase, each fix from a code review.
