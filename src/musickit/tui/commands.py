"""Custom command-palette provider (Ctrl+P) for playback verbs."""

from __future__ import annotations

from collections.abc import Callable

from textual.command import DiscoveryHit, Hit, Hits, Provider


class MusickitCommands(Provider):
    """Custom Ctrl+P palette entries for playback / navigation actions.

    Textual's default palette only surfaces App-level keybindings. Adding
    a Provider lets us list player verbs in the searchable picker so the
    user doesn't have to remember `n` / `p` / `<` / `>` / `s` / `r` etc.
    """

    @property
    def _commands(self) -> list[tuple[str, str, str]]:
        return [
            ("Play / Pause", "action_toggle_pause", "Toggle playback (Space)"),
            ("Next track", "action_next_track", "Skip to next (n)"),
            ("Previous track", "action_prev_track", "Go to previous (p)"),
            ("Seek forward 5s", "action_seek_fwd", "+5 seconds (>)"),
            ("Seek backward 5s", "action_seek_back", "-5 seconds (<)"),
            ("Volume up", "action_vol_up", "+5% (+)"),
            ("Volume down", "action_vol_down", "-5% (-)"),
            ("Toggle shuffle", "action_toggle_shuffle", "On / Off (s)"),
            ("Cycle repeat mode", "action_cycle_repeat", "Off → Album → Track (r)"),
            ("Toggle fullscreen", "action_toggle_fullscreen", "Hide library, expand visualizer (f)"),
            ("Toggle help panel", "action_toggle_help", "Show / hide the keybindings reference (?)"),
            ("Rescan library", "action_rescan_library", "Re-walk the library root (Ctrl+R)"),
            ("Quit", "action_quit", "Exit musickit (q)"),
        ]

    async def discover(self) -> Hits:
        """Run the same commands when the palette is opened with no query."""
        for name, action_name, help_text in self._commands:
            yield DiscoveryHit(
                name,
                self._action(action_name),
                help=help_text,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, action_name, help_text in self._commands:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    self._action(action_name),
                    help=help_text,
                )

    def _action(self, action_name: str) -> Callable[[], None]:
        app = self.app

        def runner() -> None:
            method = getattr(app, action_name, None)
            if callable(method):
                method()

        return runner
