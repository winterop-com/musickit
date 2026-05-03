"""Per-album outcome model + end-of-run summary table."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict
from rich.console import Console
from rich.table import Table

from musickit.cover import CoverSource


class AlbumReport(BaseModel):
    """Per-album outcome line shown at the end of a run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    input_dir: Path
    output_dir: Path | None
    artist: str
    album: str
    track_count: int
    cover_source: CoverSource | None
    cover_size: str
    warnings: list[str]
    error: str | None = None
    input_bytes: int = 0
    output_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def saved_ratio(self) -> float | None:
        """Fraction of input size saved (0.0 to 1.0). None if no output was produced."""
        if self.input_bytes == 0 or self.output_bytes == 0:
            return None
        return 1.0 - (self.output_bytes / self.input_bytes)


def _format_bytes(n: int) -> str:
    """Human-readable size (B/KB/MB/GB/TB) with reasonable precision."""
    if n <= 0:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size >= 10 else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _print_summary(console: Console, reports: list[AlbumReport]) -> None:
    table = Table(title="Audio convert — summary", show_lines=False)
    table.add_column("Status")
    table.add_column("Artist")
    table.add_column("Album")
    table.add_column("Tracks", justify="right")
    table.add_column("Cover")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Saved", justify="right")
    table.add_column("Notes")

    total_input = 0
    total_output = 0
    for r in reports:
        status = "[green]ok[/green]" if r.ok else "[red]fail[/red]"
        notes = "; ".join(r.warnings) or ("[red]" + r.error + "[/red]" if r.error else "")
        saved = f"{r.saved_ratio * 100:.0f}%" if r.saved_ratio is not None else "—"
        table.add_row(
            status,
            r.artist,
            r.album,
            str(r.track_count),
            r.cover_size,
            _format_bytes(r.input_bytes),
            _format_bytes(r.output_bytes),
            saved,
            notes,
        )
        total_input += r.input_bytes
        total_output += r.output_bytes

    if reports:
        total_saved = (
            f"{(1.0 - total_output / total_input) * 100:.0f}%" if total_input > 0 and total_output > 0 else "—"
        )
        table.add_section()
        table.add_row(
            "[bold]total[/bold]",
            "",
            "",
            str(sum(r.track_count for r in reports)),
            "",
            f"[bold]{_format_bytes(total_input)}[/bold]",
            f"[bold]{_format_bytes(total_output)}[/bold]",
            f"[bold]{total_saved}[/bold]",
            "",
        )

    console.print(table)
