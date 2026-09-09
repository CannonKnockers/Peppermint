"""The Peppermint window application.

The application owns the bus name `org.peppermint.Window`. The hotkey command
`peppermint toggle` calls `Toggle` on that name. Only one window can exist.
"""

from __future__ import annotations

import logging
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from peppermint import config  # noqa: E402
from peppermint.common import dbus_api  # noqa: E402
from peppermint.ui.tray import Tray  # noqa: E402
from peppermint.ui.window import PeppermintWindow  # noqa: E402

log = logging.getLogger("peppermint.ui")


class PeppermintApp(Gtk.Application):
    def __init__(self, background: bool = False):
        super().__init__(application_id="org.peppermint.App",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: PeppermintWindow | None = None
        self.tray: Tray | None = None
        self.background = background
        self.registration_id = 0

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLib.set_application_name(config.APP_NAME)
        Gio.bus_own_name(
            Gio.BusType.SESSION,
            dbus_api.WINDOW_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            lambda *_: log.warning("Another Peppermint window already runs."),
        )
        try:
            self.tray = Tray(self)
        except Exception:
            log.exception("Peppermint could not make the panel icon.")

    def do_shutdown(self) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None
        Gtk.Application.do_shutdown(self)

    def do_activate(self) -> None:
        # The panel icon keeps Peppermint alive when no window is open.
        self.hold()
        if not self.background:
            self.present_window()

    # --- D-Bus -------------------------------------------------------------

    def _on_bus_acquired(self, connection, _name) -> None:
        node = Gio.DBusNodeInfo.new_for_xml(dbus_api.WINDOW_XML)
        self.registration_id = connection.register_object(
            dbus_api.WINDOW_PATH, node.interfaces[0], self._on_method, None, None
        )

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "Toggle":
            GLib.idle_add(self.toggle)
        elif method == "Present":
            GLib.idle_add(self.present_window)
        elif method == "OpenTask":
            task_id = params.unpack()[0]
            GLib.idle_add(self._open_task, int(task_id))
        invocation.return_value(None)

    # --- window ------------------------------------------------------------

    def _ensure_window(self) -> PeppermintWindow:
        if self.window is None:
            self.window = PeppermintWindow(self)
        return self.window

    def present_window(self) -> bool:
        window = self._ensure_window()
        window.show_all()
        window.present_with_time(Gtk.get_current_event_time() or GLib.get_monotonic_time() // 1000)
        page = window.pages.get_visible_child_name()
        if page == "conversations":
            entry = window.entry if window._active_task_id is None else window.conversation_entry
            entry.grab_focus()
        elif page == "tasks":
            window.task_board.search.grab_focus()
        elif page == "diagnostics":
            window.diagnostics.start_button.grab_focus()
        else:
            window.menu_button.grab_focus()
        return GLib.SOURCE_REMOVE

    def toggle(self) -> bool:
        window = self._ensure_window()
        if window.is_visible() and window.is_active():
            window.hide()
        else:
            window.refresh()
            self.present_window()
        return GLib.SOURCE_REMOVE

    def _open_task(self, task_id: int) -> bool:
        self.present_window()
        if self.window:
            self.window.open_task(task_id)
        return GLib.SOURCE_REMOVE


def main() -> int:
    """Start the window. `--background` starts only the panel icon."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    background = "--background" in sys.argv
    argv = [a for a in sys.argv if a != "--background"]
    return PeppermintApp(background=background).run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
