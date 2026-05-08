"""Polling helpers for Textual TUI tests.

Many TUI tests look for a specific row in `BrowserList` shortly after the
app boots. A single `await pilot.pause()` usually suffices on a developer
laptop, but on CI the BrowserList may not be populated yet, and a naive
`next(c for c in browser.children if ...)` raises `StopIteration` — which
under PEP 479 surfaces as `RuntimeError: coroutine raised StopIteration`.

`wait_for_browser_child` retries the lookup across a few `pilot.pause()`
cycles before giving up with a clear assertion message.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


async def wait_for_browser_child(
    pilot: object,
    children: Callable[[], Iterable[T]],
    predicate: Callable[[T], bool],
    *,
    attempts: int = 20,
    description: str = "matching child",
) -> T:
    """Poll `pilot.pause()` until `predicate(child)` is True for some child.

    Args:
        pilot: Textual `Pilot` (typed loosely to avoid coupling test files).
        children: Callable returning the current child iterable. Called fresh
            on each attempt, so widgets re-rendered between pauses are picked up.
        predicate: Filter applied to each child; first match is returned.
        attempts: Maximum number of `pilot.pause()` cycles to wait for.
        description: Human-readable label used in the failure message.

    Returns:
        The first child for which `predicate(child)` is True.

    Raises:
        AssertionError: If no child matches after `attempts` pauses.
    """
    for _ in range(attempts):
        for child in children():
            if predicate(child):
                return child
        await pilot.pause()  # type: ignore[attr-defined]
    raise AssertionError(f"never found {description} after {attempts} pauses")
