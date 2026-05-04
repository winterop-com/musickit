"""In-TUI AirPlay device picker.

Modal screen: lists discovered devices + a 'Local audio' entry, selection
switches the player's AirPlay target. Discovery runs on a worker thread
so the UI doesn't freeze during the ~3-second mDNS scan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

if TYPE_CHECKING:
    from musickit.tui.airplay import AirPlayDevice
    from musickit.tui.app import MusickitApp


class AirPlayPickerScreen(ModalScreen[None]):
    """Choose an AirPlay output device, or fall back to Local audio."""

    BINDINGS = [Binding("escape", "dismiss_screen", "Cancel", show=False)]

    DEFAULT_CSS = """
    AirPlayPickerScreen {
        align: center middle;
    }
    AirPlayPickerScreen Vertical {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    AirPlayPickerScreen #title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    AirPlayPickerScreen #status {
        margin-bottom: 1;
        color: $text-muted;
    }
    """

    def __init__(self, app_ref: MusickitApp) -> None:
        super().__init__()
        self._app_ref = app_ref

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical():
            yield Label("AirPlay Output", id="title")
            yield Label("Scanning…", id="status")
            yield ListView(id="devices")

    def on_mount(self) -> None:  # noqa: D102
        self._discover_async()

    @work(thread=True, exclusive=True, group="airplay-discover")
    def _discover_async(self) -> None:
        """Run pyatv.scan on the AirPlay controller's loop, off the UI thread."""
        controller = self._app_ref.get_or_create_airplay()
        try:
            devices = controller.discover()
        except Exception as exc:  # pragma: no cover — discovery is best-effort
            self.app.call_from_thread(self._on_discover_failed, str(exc))
            return
        self.app.call_from_thread(self._populate, devices)

    def _populate(self, devices: list[AirPlayDevice]) -> None:
        list_view = self.query_one("#devices", ListView)
        list_view.clear()

        # First entry: local audio (= disable AirPlay routing).
        local_item = ListItem(Static("[cyan]●[/] Local audio (this Mac)"))
        local_item.airplay_target = None  # type: ignore[attr-defined]
        list_view.append(local_item)

        if not devices:
            none_item = ListItem(Static("[dim]no AirPlay devices found on the LAN[/]"))
            none_item.airplay_target = None  # type: ignore[attr-defined]
            list_view.append(none_item)
        else:
            from musickit.tui.airplay import AirPlayDevice as _AirPlayDevice  # type: ignore[unused-ignore]

            current_id = (
                self._app_ref.airplay.device.identifier
                if self._app_ref.airplay is not None and self._app_ref.airplay.device is not None
                else None
            )
            for d in devices:
                marker = "[bold green]▶[/]" if d.identifier == current_id else "[cyan]♪[/]"
                item = ListItem(Static(f"{marker} {d.name}  [dim]{d.address}[/]"))
                item.airplay_target = d  # type: ignore[attr-defined]
                list_view.append(item)
            _ = _AirPlayDevice  # appease pyright unused-import in branch

        self.query_one("#status", Label).update(f"Found {len(devices)} device(s)")
        list_view.focus()
        list_view.index = 0

    def _on_discover_failed(self, message: str) -> None:
        self.query_one("#status", Label).update(f"[red]Discovery failed: {message}[/]")

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # noqa: D102
        target = getattr(event.item, "airplay_target", None)
        self._app_ref.switch_airplay(target)
        self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        """Close the picker without changing routing."""
        self.dismiss(None)
