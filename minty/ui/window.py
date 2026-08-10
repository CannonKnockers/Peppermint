"""The Minty window: an idea box on top and a task list below."""

from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from minty.common import dbus_api  # noqa: E402
from minty.ui.task_row import TaskRow  # noqa: E402

CSS = b"""
.pill {
    padding: 2px 9px;
    border-radius: 9px;
    font-size: 0.8em;
}
.pill-idle { background: alpha(@theme_fg_color, 0.12); }
.pill-busy { background: alpha(#3584e4, 0.28); }
.pill-wait { background: alpha(#f5c211, 0.35); }
.pill-ok   { background: alpha(#33d17a, 0.28); }
.pill-bad  { background: alpha(#e01b24, 0.28); }

.step-text  { font-family: monospace; font-size: 0.85em; opacity: 0.85; }
.step-ok      { color: #33d17a; }
.step-error   { color: #e01b24; }
.step-denied  { color: #e01b24; }
.step-pending { color: #f5c211; }
.step-asked   { color: #f5c211; }
.mono { font-family: monospace; font-size: 0.85em; }

.approval { border-radius: 6px; background: alpha(#f5c211, 0.12); }
.result { }
.error  { color: #e01b24; }

.idea-entry { font-size: 1.05em; padding: 8px; }
.empty-hint { opacity: 0.55; }
"""


class MintyWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Minty")
        self.set_default_size(560, 680)
        self.set_icon_name("system-run")
        self.rows: dict[int, TaskRow] = {}
        self._detail_pending: set[int] = set()

        self._load_css()
        self._build()
        self._subscribe()
        self.connect("delete-event", self._on_close)
        self.connect("key-press-event", self._on_key)
        self.refresh()

    # --- layout ------------------------------------------------------------

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build(self) -> None:
        header = Gtk.HeaderBar(title="Minty", show_close_button=True)
        header.set_subtitle("your helper on Linux Mint")
        self.set_titlebar(header)

        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Refresh the list")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)

        self.status_dot = Gtk.Label()
        header.pack_start(self.status_dot)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(box)

        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry_box.set_margin_start(12)
        entry_box.set_margin_end(12)
        entry_box.set_margin_top(12)
        entry_box.set_margin_bottom(8)
        box.pack_start(entry_box, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Toss an idea to Minty...")
        self.entry.get_style_context().add_class("idea-entry")
        self.entry.connect("activate", self._on_submit)
        entry_box.pack_start(self.entry, True, True, 0)

        send = Gtk.Button(label="Go")
        send.get_style_context().add_class("suggested-action")
        send.connect("clicked", self._on_submit)
        entry_box.pack_start(send, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.pack_start(scroller, True, True, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", lambda _lb, row: row.toggle())
        self.list.set_placeholder(self._placeholder())
        scroller.add(self.list)

    @staticmethod
    def _placeholder() -> Gtk.Widget:
        label = Gtk.Label(
            label="No tasks yet.\n\nType an idea above, for example:\n"
                  "\"sort my Downloads folder by file type\"\n"
                  "\"make my desktop theme light\"\n"
                  "\"how much disk space is left?\""
        )
        label.set_justify(Gtk.Justification.CENTER)
        label.get_style_context().add_class("empty-hint")
        label.set_margin_top(40)
        label.show()
        return label

    # --- the daemon --------------------------------------------------------

    def _subscribe(self) -> None:
        bus = dbus_api.session_bus()
        bus.signal_subscribe(
            None, dbus_api.DAEMON_IFACE, "TaskUpdated", dbus_api.DAEMON_PATH,
            None, 0, self._on_task_updated,
        )

    def _on_task_updated(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        task_id, _status = params.unpack()
        GLib.idle_add(self._refresh_one, int(task_id))

    def _refresh_one(self, task_id: int) -> bool:
        task = self._get_task(task_id)
        if task is None:
            return GLib.SOURCE_REMOVE
        row = self.rows.get(task_id)
        if row is None:
            self.refresh()
        else:
            row.update(task)
        return GLib.SOURCE_REMOVE

    def _get_task(self, task_id: int) -> dict | None:
        try:
            result = dbus_api.call_daemon("GetTask", GLib.Variant("(i)", (task_id,)),
                                          GLib.VariantType("(s)"), timeout=10000)
        except Exception:
            return None
        payload = json.loads(result.unpack()[0])
        return payload if payload else None

    def refresh(self) -> None:
        try:
            result = dbus_api.call_daemon("ListTasks", GLib.Variant("(i)", (40,)),
                                          GLib.VariantType("(s)"), timeout=10000)
        except dbus_api.DaemonNotRunning:
            self.status_dot.set_markup("<span foreground='#e01b24'>daemon off</span>")
            return
        except Exception:
            return

        self.status_dot.set_text("")
        tasks = json.loads(result.unpack()[0])
        seen = set()

        for index, task in enumerate(tasks):
            task_id = int(task["id"])
            seen.add(task_id)
            row = self.rows.get(task_id)
            if row is None:
                row = TaskRow(task, self)
                self.rows[task_id] = row
                self.list.insert(row, index)
                row.show_all()
            else:
                # An open row needs the steps, which ListTasks does not send.
                if row.expanded:
                    task = self._get_task(task_id) or task
                row.update(task)

        for task_id in list(self.rows):
            if task_id not in seen:
                self.list.remove(self.rows.pop(task_id))

    def request_detail(self, task_id: int) -> None:
        """A row opened. Fetch its steps."""
        task = self._get_task(task_id)
        row = self.rows.get(task_id)
        if task and row:
            row.update(task)

    # --- actions -----------------------------------------------------------

    def _on_submit(self, *_args) -> None:
        idea = self.entry.get_text().strip()
        if not idea:
            return
        try:
            dbus_api.call_daemon("AddTask", GLib.Variant("(s)", (idea,)),
                                 GLib.VariantType("(i)"), timeout=10000)
        except dbus_api.DaemonNotRunning as exc:
            self._error_dialog(str(exc))
            return
        self.entry.set_text("")
        self.refresh()

    def confirm(self, task_id: int, approved: bool) -> None:
        dbus_api.call_daemon("Confirm", GLib.Variant("(ib)", (task_id, approved)), timeout=10000)

    def answer(self, task_id: int, text: str) -> None:
        dbus_api.call_daemon("Answer", GLib.Variant("(is)", (task_id, text)), timeout=10000)

    def chat(self, task_id: int, text: str) -> None:
        dbus_api.call_daemon("Chat", GLib.Variant("(is)", (task_id, text)), timeout=10000)

    def open_task(self, task_id: int) -> None:
        self.refresh()
        row = self.rows.get(task_id)
        if row:
            row.expand()

    def _error_dialog(self, message: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK, text="Minty cannot reach the daemon")
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    # --- window behaviour --------------------------------------------------

    def _on_close(self, *_args) -> bool:
        """The close button hides the window. The daemon keeps working."""
        self.hide()
        return True

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False
