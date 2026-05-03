"""Shared progress-reporting handles passed down through the album/track loops."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from rich.progress import Progress, TaskID


class ProgressContext(BaseModel):
    """Bundle of progress reporting handles passed down into per-album work."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    progress: Progress | None = None
    albums_task: TaskID | None = None
    tracks_task: TaskID | None = None
    verbose: bool = False
