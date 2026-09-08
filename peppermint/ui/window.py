"""The Peppermint window: an idea box on top and a task list below."""

from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from peppermint.common import dbus_api  # noqa: E402
from peppermint.ui.task_row import TaskRow  # noqa: E402

CSS = b"""
.peppermint-window {
    background: #1d2422;
    color: #e8eeeb;
    font-family: Cantarell, Sans;
    font-size: 14px;
}
.peppermint-window headerbar {
    background: #242c29;
    color: #e8eeeb;
    border-bottom: 1px solid #39443e;
    box-shadow: none;
    padding: 10px 14px;
}
.peppermint-window headerbar .subtitle { color: #a9b8b0; }
.peppermint-window button {
    background-image: none;
    background-color: #34413a;
    color: #e7efea;
    border: 1px solid #4b5a51;
    border-radius: 10px;
    padding: 9px 14px;
    box-shadow: none;
    text-shadow: none;
    transition: background-color 160ms ease, border-color 160ms ease;
}
.peppermint-window button:hover { background-color: #415248; border-color: #759b86; }
.peppermint-window button:active { background-color: #4b6053; }
.peppermint-window button:focus { border-color: #a7dfbc; }
.peppermint-window button:disabled { opacity: 0.5; }
.peppermint-window button.suggested-action {
    background-color: #a7dfbc;
    color: #182b20;
    border-color: #a7dfbc;
    font-weight: bold;
}
.peppermint-window button.suggested-action:hover { background-color: #c3ecd1; }
.peppermint-window button.suggested-action:active { background-color: #89c8a1; }
.peppermint-window entry {
    background: #202823;
    color: #eef4ef;
    caret-color: #a7dfbc;
    border: 1px solid #4a594f;
    border-radius: 10px;
    box-shadow: none;
    padding: 11px 13px;
    min-height: 20px;
}
.peppermint-window entry:focus { border-color: #a7dfbc; box-shadow: 0 0 0 1px #a7dfbc; }
.peppermint-window entry selection { background: #a7dfbc; color: #182b20; }
.peppermint-window scrolledwindow, .peppermint-window viewport,
.peppermint-window list { background: transparent; border: none; }
.peppermint-window scrollbar { background: transparent; }
.peppermint-window scrollbar slider { background: #516257; border: none; border-radius: 8px; min-width: 6px; }
.peppermint-window row { background: transparent; padding: 0; }
.peppermint-window row:hover { background: transparent; }
.peppermint-window .composer { background: #29352e; border: 1px solid #455b4b; border-radius: 16px; padding: 20px; }
.peppermint-window .hero-title { font-size: 25px; font-weight: bold; color: #eff6f0; }
.peppermint-window .muted { color: #b4c1b8; font-size: 13px; }
.peppermint-window .section-title { font-size: 12px; font-weight: bold; color: #a8c4b2; letter-spacing: 1px; }
.peppermint-window .task-card { background: #29312d; border: 1px solid #414c45; border-radius: 14px; }
.peppermint-window .task-title { font-weight: bold; font-size: 15px; }
.peppermint-window .conversation { border-top: 1px solid #414c45; padding-top: 16px; }
.peppermint-window .message { border-radius: 10px; padding: 14px 16px; }
.peppermint-window .message-user { background: #35453b; border-left: 3px solid #a7dfbc; }
.peppermint-window .message-assistant { background: #252d28; border: 1px solid #3b4840; }
.peppermint-window .speaker { color: #afd4bb; font-size: 11px; font-weight: bold; letter-spacing: 1px; }
.peppermint-window .result { color: #e5eee7; }
.peppermint-window .pill { padding: 5px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }
.peppermint-window .pill-idle { background: #404b44; color: #d0dbd3; }
.peppermint-window .pill-busy { background: #355447; color: #c0e7cc; }
.peppermint-window .pill-wait { background: #5b5032; color: #f2d997; }
.peppermint-window .pill-ok { background: #33503d; color: #bce6c9; }
.peppermint-window .pill-bad { background: #5a3a39; color: #f1b9b3; }
.peppermint-window .step-text, .peppermint-window .mono { font-family: monospace; font-size: 12px; color: #c5d4ca; }
.peppermint-window .step-ok { color: #a7dfbc; }
.peppermint-window .step-error, .peppermint-window .step-denied,
.peppermint-window .error { color: #f1b9b3; }
.peppermint-window .step-pending, .peppermint-window .step-asked { color: #ead094; }
.peppermint-window .approval { background: #373b2b; border: 1px solid #777347; border-radius: 12px; }
.peppermint-window .approval > border { border: none; }
.peppermint-window .activity { color: #b4c1b8; padding: 7px 0; }
.peppermint-window .empty-hint { color: #b4c1b8; }
.peppermint-window .example { background: #29312d; border-color: #414c45; padding: 14px; }
.peppermint-window .stop-button { padding: 4px 10px; background: transparent; font-size: 12px; }
"""


class PeppermintWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Peppermint")
        self.set_default_size(940, 820)
        self.set_size_request(660, 560)
        self.get_style_context().add_class("peppermint-window")
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
        header = Gtk.HeaderBar(title="Peppermint", show_close_button=True)
        header.set_subtitle("Your local Linux assistant")
        self.set_titlebar(header)

        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Refresh conversations")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)

        self.status_dot = Gtk.Label(label="Connecting")
        self.status_dot.get_style_context().add_class("muted")
        header.pack_start(self.status_dot)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_margin_top(24)
        box.set_margin_bottom(16)
        self.add(box)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        composer.get_style_context().add_class("composer")
        box.pack_start(composer, False, False, 0)
        title = Gtk.Label(label="What would you like to solve?", xalign=0)
        title.get_style_context().add_class("hero-title")
        composer.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label="Explore solutions. Make a plan. Approve each action.", xalign=0)
        subtitle.get_style_context().add_class("muted")
        composer.pack_start(subtitle, False, False, 0)

        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        entry_box.set_margin_top(6)
        composer.pack_start(entry_box, False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Describe an issue or something you want to do…")
        self.entry.get_style_context().add_class("idea-entry")
        self.entry.connect("activate", self._on_submit)
        entry_box.pack_start(self.entry, True, True, 0)
        send = Gtk.Button(label="New conversation")
        send.get_style_context().add_class("suggested-action")
        send.connect("clicked", self._on_submit)
        entry_box.pack_start(send, False, False, 0)

        section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label="CONVERSATIONS", xalign=0)
        label.get_style_context().add_class("section-title")
        section.pack_start(label, True, True, 0)
        self.conversation_count = Gtk.Label(label="0 saved locally")
        self.conversation_count.get_style_context().add_class("muted")
        section.pack_end(self.conversation_count, False, False, 0)
        box.pack_start(section, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.pack_start(scroller, True, True, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", lambda _lb, row: row.toggle())
        self.list.set_placeholder(self._placeholder())
        scroller.add(self.list)

        footer = Gtk.Label(label="Runs on your computer  ·  Every action asks for permission", xalign=0)
        footer.get_style_context().add_class("muted")
        box.pack_start(footer, False, False, 0)

    def _placeholder(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        title = Gtk.Label(label="A calmer way to work with your computer", xalign=0)
        title.get_style_context().add_class("task-title")
        box.pack_start(title, False, False, 0)
        hint = Gtk.Label(label="Start with an idea, or try one of these:", xalign=0)
        hint.get_style_context().add_class("empty-hint")
        box.pack_start(hint, False, False, 0)
        examples = [
            ("Videos + AI, together", "Show me solutions to run an X video, a YouTube or Netflix video, "
             "and 1–2 AI prompts at the same time on this computer."),
            ("Find what is slowing things down", "Help me find why my computer is slow and compare solutions."),
            ("Make room for what matters", "Help me find what is using disk space and show me safe cleanup options."),
        ]
        for title_text, prompt in examples:
            button = Gtk.Button(label=title_text)
            button.get_child().set_xalign(0)
            button.get_style_context().add_class("example")
            button.set_tooltip_text(prompt)
            button.connect("clicked", lambda _button, text=prompt: self._use_example(text))
            box.pack_start(button, False, False, 0)
        box.show_all()
        return box

    def _use_example(self, prompt: str) -> None:
        self.entry.set_text(prompt)
        self.entry.grab_focus()
        self.entry.set_position(-1)

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
            self.status_dot.set_text("● Offline")
            return
        except Exception:
            return

        self.status_dot.set_text("● Connected")
        tasks = json.loads(result.unpack()[0])
        self.conversation_count.set_text(f"{len(tasks)} saved locally")
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
            result = dbus_api.call_daemon("AddTask", GLib.Variant("(s)", (idea,)),
                                 GLib.VariantType("(i)"), timeout=10000)
        except dbus_api.DaemonNotRunning as exc:
            self._error_dialog(str(exc))
            return
        self.entry.set_text("")
        self.refresh()
        self.open_task(int(result.unpack()[0]))

    def confirm(self, task_id: int, approved: bool, confirmation_id: int | None = None) -> None:
        if confirmation_id is not None:
            dbus_api.call_daemon("ConfirmAction", GLib.Variant("(iib)", (task_id, confirmation_id, approved)), timeout=10000)
        else:
            dbus_api.call_daemon("Confirm", GLib.Variant("(ib)", (task_id, approved)), timeout=10000)

    def cancel(self, task_id: int) -> None:
        dbus_api.call_daemon("Cancel", GLib.Variant("(i)", (task_id,)), timeout=10000)
        self.request_detail(task_id)

    def answer(self, task_id: int, text: str) -> None:
        dbus_api.call_daemon("Answer", GLib.Variant("(is)", (task_id, text)), timeout=10000)

    def chat(self, task_id: int, text: str) -> None:
        dbus_api.call_daemon("Chat", GLib.Variant("(is)", (task_id, text)), timeout=10000)
        self.request_detail(task_id)

    def open_task(self, task_id: int) -> None:
        self.refresh()
        row = self.rows.get(task_id)
        if row:
            row.expand()

    def _error_dialog(self, message: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK, text="Peppermint cannot reach the daemon")
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
