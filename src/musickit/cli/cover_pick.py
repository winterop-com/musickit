"""`musickit cover-pick` — semi-automated cover sourcing via musichoarders.xyz.

Walks the library, surfaces albums missing a cover (or with a low-res one),
opens https://covers.musichoarders.xyz/ pre-filled with the album's
artist+title in the user's browser, and accepts the user-pasted image URL
to download + save into the album dir.

Per the musichoarders integration policy at
https://covers.musichoarders.xyz/ this is the supported path: open in a
compatible web view and let the user interact. We never scrape the site
ourselves.
"""

from __future__ import annotations

import io
import webbrowser
from pathlib import Path
from typing import Annotated

import httpx
import typer
from PIL import Image
from rich.console import Console

from musickit.cli import app
from musickit.cli._scan import scan_with_progress
from musickit.cover import DEFAULT_MAX_EDGE
from musickit.enrich.musichoarders import build_search_url
from musickit.library import LibraryAlbum, audit
from musickit.metadata import SUPPORTED_AUDIO_EXTS, embed_cover_only

_LOW_RES_THRESHOLD_PIXELS = 500 * 500


@app.command(name="cover-pick")
def cover_pick(
    target_dir: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="Library root or single album directory.",
        ),
    ] = Path("./output"),
    issues_only: Annotated[
        bool,
        typer.Option(
            "--issues-only/--all",
            help="Only show albums with no/low-res cover. --all picks for every album.",
        ),
    ] = True,
    embed: Annotated[
        bool,
        typer.Option(
            "--embed/--no-embed",
            help="After saving cover.jpg, also embed it into every track in the album.",
        ),
    ] = True,
    max_edge: Annotated[
        int,
        typer.Option("--cover-max-edge", min=128, help="Resize to fit this max long-edge."),
    ] = DEFAULT_MAX_EDGE,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Print the musichoarders URL instead of opening it."),
    ] = False,
) -> None:
    """Manually source cover art for albums missing one, via musichoarders.

    For each candidate album:
      1. Print the album line + audit reason.
      2. Open the musichoarders pre-fill URL in your browser.
      3. Click any cover on the site to copy its URL (musichoarders' UI does this).
      4. Paste the URL back into the terminal — `s` to skip, `q` to quit.
      5. We download, validate, resize, save as `cover.jpg`, and (with --embed)
         re-embed into every track.
    """
    console = Console()

    candidates = _collect_candidates(console, target_dir, issues_only=issues_only)
    if not candidates:
        console.print(f"[green]nothing to pick[/green] — every album under {target_dir} has a usable cover")
        raise typer.Exit(0)

    console.print(f"[cyan]{len(candidates)} album(s) to review[/cyan]\n")

    http = httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        for i, album in enumerate(candidates, start=1):
            _process_album(
                console,
                http,
                album,
                position=i,
                total=len(candidates),
                embed=embed,
                max_edge=max_edge,
                no_browser=no_browser,
            )
    finally:
        http.close()


def _collect_candidates(console: Console, target_dir: Path, *, issues_only: bool) -> list[LibraryAlbum]:
    index = scan_with_progress(
        console,
        target_dir,
        measure_pictures=True,
        description="[cyan]Scanning for missing covers",
    )
    audit(index)
    if not issues_only:
        return list(index.albums)
    return [
        a for a in index.albums if not a.has_cover or (a.cover_pixels and a.cover_pixels < _LOW_RES_THRESHOLD_PIXELS)
    ]


def _process_album(
    console: Console,
    http: httpx.Client,
    album: LibraryAlbum,
    *,
    position: int,
    total: int,
    embed: bool,
    max_edge: int,
    no_browser: bool,
) -> None:
    artist = album.tag_album_artist or album.artist_dir
    title = album.tag_album or album.album_dir
    reason = _audit_reason(album)
    console.print(f"[bold]{position}/{total}[/] {artist} — {title}  [dim]({reason})[/]")

    url = build_search_url(artist, title, resolution=max_edge)
    if no_browser:
        console.print(f"  [dim]URL:[/] {url}")
    else:
        console.print(f"  [dim]opening[/] {url}")
        try:
            webbrowser.open(url)
        except Exception as exc:  # pragma: no cover — headless env
            console.print(f"  [yellow]could not open browser: {exc}[/yellow]")
            console.print(f"  [dim]URL:[/] {url}")

    while True:
        answer = typer.prompt("  Cover URL (s=skip, q=quit)", default="s", show_default=False)
        answer = answer.strip()
        if answer.lower() == "q":
            console.print("  [yellow]quitting[/]")
            raise typer.Exit(0)
        if answer.lower() in ("", "s", "skip"):
            console.print("  [dim]skipped[/]\n")
            return
        if not (answer.startswith("http://") or answer.startswith("https://")):
            console.print("  [red]not a URL — paste the image URL or 's' to skip[/]")
            continue
        try:
            data = _download(http, answer)
            normalised, mime, dims = _normalise(data, max_edge=max_edge)
        except Exception as exc:
            console.print(f"  [red]failed:[/] {exc}")
            continue

        target = album.path / "cover.jpg"
        target.write_bytes(normalised)
        console.print(f"  [green]saved[/] {target.relative_to(album.path.parent.parent)} ({dims[0]}x{dims[1]} {mime})")

        if embed:
            embedded = 0
            for audio in sorted(album.path.iterdir()):
                if audio.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
                    continue
                try:
                    embed_cover_only(audio, cover_bytes=normalised, cover_mime=mime)
                    embedded += 1
                except Exception as exc:
                    console.print(f"    [red]embed failed for {audio.name}:[/] {exc}")
            console.print(f"  [green]embedded[/] into {embedded} track(s)")
        console.print("")
        return


def _audit_reason(album: LibraryAlbum) -> str:
    if not album.has_cover:
        return "no cover"
    if album.cover_pixels and album.cover_pixels < _LOW_RES_THRESHOLD_PIXELS:
        return f"low-res cover ({album.cover_pixels}px)"
    return "manual pick"


def _download(http: httpx.Client, url: str) -> bytes:
    """Fetch the image bytes. Raises on HTTP error or non-image content-type."""
    response = http.get(url)
    response.raise_for_status()
    ct = response.headers.get("content-type", "").lower()
    if "image/" not in ct and not (url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
        raise RuntimeError(f"not an image (content-type: {ct or 'unknown'})")
    return response.content


def _normalise(data: bytes, *, max_edge: int) -> tuple[bytes, str, tuple[int, int]]:
    """Validate via Pillow, resize to fit `max_edge`, re-encode as JPEG (or PNG if alpha)."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image.thumbnail((max_edge, max_edge))
        out = io.BytesIO()
        if image.mode == "RGBA":
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png", image.size
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        rgb.save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue(), "image/jpeg", image.size
