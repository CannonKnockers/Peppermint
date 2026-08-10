"""The Minty panel indicator.

The icon sits with the other indicators on the Cinnamon panel, next to the
network, volume, and battery buttons. It uses `XApp.StatusIcon`, the same
interface that the Mint update manager and printer indicators use, so it
looks and behaves like the rest of the panel.

The icon changes with the state of the work:

    idle       Minty waits for an idea
    working    Minty runs a task now
    attention  Minty needs your approval or your answer

A left click opens the window. A right click opens the menu.
"""

from __future__ import annotations

import json
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from minty.common import dbus_api  # noqa: E402
from minty.common.models import Status  # noqa: E402

log = logging.getLogger("minty.tray")

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp

    HAVE_XAPP = True
except (ImportError, ValueError):  # a desktop without the Mint libraries
    HAVE_XAPP = False

ICON_IDLE = "system-run-symbolic"
ICON_WORKING = "content-loading-symbolic"
ICON_ATTENTION = "dialog-warning-symbolic"
ICON_FAILED = "dialog-error-symbolic"

STATUS_WORD = {
    "queued": "waiting",
    "planning": "thinking",
    "running": "working",
    "awaiting-confirmation": "needs your approval",
    "awaiting-input": "has a question",
    "done": "done",
    "failed": "failed",
    "cancelled": "stopped",
}

MENU_TASK_COUNT = 6


def shorten(text: str, limit: int = 44) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def pick_icon(tasks: list[dict]) -> tuple[str, str]:
    """Choose the panel icon and the tooltip from the task list.

    A task that waits for the user wins over a task that runs, because the
    user must act before Minty can continue.
    """
    if not tasks:
        return ICON_IDLE, "Minty — ready for an idea"

    waiting = [t for t in tasks if Status(t["status"]).needs_user]
    if waiting:
        first = waiting[0]
        word = STATUS_WORD[first["status"]]
        extra = f" (+{len(waiting) - 1} more)" if len(waiting) > 1 else ""
        return ICON_ATTENTION, f"Minty {word}:\n{shorten(first['idea'], 60)}{extra}"

    busy = [t for t in tasks if Status(t["status"]).is_active]
    if busy:
        return ICON_WORKING, f"Minty is working:\n{shorten(busy[0]['idea'], 60)}"

    last = tasks[0]
    word = STATUS_WORD.get(last["status"], last["status"])
    icon = ICON_FAILED if last["status"] == Status.FAILED.value else ICON_IDLE
    return icon, f"Minty — ready\nLast task: {word}"


class Tray:
    """The panel indicator. It reads the daemon and drives the window."""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self.fallback = None
        self._state = ""
        self._build_icon()
        self._subscribe()
        self.refresh()
        # The daemon may start after the panel icon. Check again now and then.
        GLib.timeout_add_seconds(30, self._tick)

    # --- the icon ----------------------------------------------------------

    def _build_icon(self) -> None:
        if HAVE_XAPP:
            self.icon = XApp.StatusIcon()
            self.icon.set_name("minty")
            self.icon.set_icon_name(ICON_IDLE)
            self.icon.set_tooltip_text("Minty")
            self.icon.connect("activate", self._on_activate)
            self.icon.set_secondary_menu(self._menu())
            self.icon.set_visible(True)
            log.info("The panel icon uses XApp.")
            return

        # A desktop without XApp still gets an icon.
        self.fallback = Gtk.StatusIcon.new_from_icon_name(ICON_IDLE)
        self.fallback.set_tooltip_text("Minty")
        self.fallback.connect("activate", self._on_activate)
        self.fallback.connect("popup-menu", self._on_popup)
        log.info("XApp is missing. The panel icon uses the old interface.")

    def _set_icon(self, name: str, tooltip: str) -> None:
        if name == self._state:
            self._set_tooltip(tooltip)
            return
        self._state = name
        if self.icon is not None:
            self.icon.set_icon_name(name)
        elif self.fallback is not None:
            self.fallback.set_from_icon_name(name)
        self._set_tooltip(tooltip)

    def _set_tooltip(self, text: str) -> None:
        if self.icon is not None:
            self.icon.set_tooltip_text(text)
        elif self.fallback is not None:
            self.fallback.set_tooltip_text(text)

    # --- clicks ------------------------------------------------------------

    def _on_activate(self, _icon, *_args) -> None:
        self.app.toggle()

    def _on_popup(self, _icon, button, time) -> None:
        menu = self._menu()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, self.fallback, button, time)

    # --- the menu ----------------------------------------------------------

    def _menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        tasks = self._tasks()

        waiting = [t for t in tasks if Status(t["status"]).needs_user]
        for task in waiting:
            word = "Approve" if task["status"] == Status.AWAITING_CONFIRMATION.value else "Answer"
            item = Gtk.MenuItem(label=f"{word}: {self._short(task['idea'])}")
            item.connect("activate", self._open_task, task["id"])
            menu.append(item)
        if waiting:
            menu.append(Gtk.SeparatorMenuItem())

        open_item = Gtk.MenuItem(label="Open Minty")
        open_item.connect("activate", lambda *_: self.app.present_window())
        menu.append(open_item)

        recent = [t for t in tasks if not Status(t["status"]).needs_user][:MENU_TASK_COUNT]
        if recent:
            menu.append(Gtk.SeparatorMenuItem())
            header = Gtk.MenuItem(label="Recent tasks")
            header.set_sensitive(False)
            menu.append(header)
            for task in recent:
                word = STATUS_WORD.get(task["status"], task["status"])
                item = Gtk.MenuItem(label=f"  {self._short(task['idea'])}  ({word})")
                item.connect("activate", self._open_task, task["id"])
                menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        health = Gtk.MenuItem(label=self._health_line())
        health.set_sensitive(False)
        menu.append(health)

        quit_item = Gtk.MenuItem(label="Hide the Minty icon")
        quit_item.connect("activate", lambda *_: self.app.quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    _short = staticmethod(shorten)

    def _open_task(self, _item, task_id: int) -> None:
        self.app.open_task(int(task_id))

    # --- the daemon --------------------------------------------------------

    def _tasks(self) -> list[dict]:
        try:
            result = dbus_api.call_daemon("ListTasks", GLib.Variant("(i)", (20,)),
                                          GLib.VariantType("(s)"), timeout=5000)
        except Exception:
            return []
        try:
            return json.loads(result.unpack()[0])
        except ValueError:
            return []

    def _health_line(self) -> str:
        try:
            result = dbus_api.call_daemon("Health", None, GLib.VariantType("(s)"), timeout=5000)
            data = json.loads(result.unpack()[0])
        except Exception:
            return "The Minty daemon is not running"
        if not data.get("ok"):
            return "Ollama does not answer"
        return f"Ready — {data.get('model', 'no model')}"

    def _subscribe(self) -> None:
        try:
            bus = dbus_api.session_bus()
        except Exception as exc:
            log.warning("The panel icon cannot reach the bus: %s", exc)
            return
        bus.signal_subscribe(
            None, dbus_api.DAEMON_IFACE, "TaskUpdated", dbus_api.DAEMON_PATH,
            None, 0, lambda *_a: GLib.idle_add(self.refresh),
        )

    def _tick(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self) -> bool:
        """Set the icon and the tooltip from the current tasks."""
        icon, tooltip = pick_icon(self._tasks())
        self._set_icon(icon, tooltip)
        self._refresh_menu()
        return GLib.SOURCE_REMOVE

    def _refresh_menu(self) -> None:
        if self.icon is not None:
            self.icon.set_secondary_menu(self._menu())
