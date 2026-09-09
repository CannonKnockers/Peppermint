"""Tasks, conversations and opt-in system diagnostics in one local workspace."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from peppermint.common import dbus_api  # noqa: E402
from peppermint.ui.main_menu import MainMenu, PAGES  # noqa: E402
from peppermint.ui.manual import UserManual  # noqa: E402
from peppermint.ui.task_row import TaskRow  # noqa: E402
from peppermint.ui.task_board import TaskBoard  # noqa: E402
from peppermint.ui.daemon_reader import DaemonReader  # noqa: E402
from peppermint.ui.diagnostics_view import DiagnosticsView  # noqa: E402


class PeppermintWindow(Gtk.ApplicationWindow):
    def __init__(self, app, *, reader=None, diagnostics=None):
        super().__init__(application=app, title="Peppermint")
        self.set_default_size(940, 820)
        self.set_size_request(660, 560)
        self.get_style_context().add_class("peppermint-window")
        self.set_icon_name("system-run")
        self.rows: dict[int, TaskRow] = {}
        self._reader = reader or DaemonReader()
        self._destroyed = False
        self._diagnostic_task_id = None
        self._opening_task_id = None
        self._diagnostics_view = diagnostics

        self._load_css()
        self._build()
        self._subscribe()
        self.connect("delete-event", self._on_close)
        self.connect("key-press-event", self._on_key)
        self.connect("map", self._sync_monitor)
        self.connect("unmap", lambda *_: self.diagnostics.set_active(False))
        self.connect("destroy", self._on_destroy)
        self.refresh()

    # --- layout ------------------------------------------------------------

    def _load_css(self) -> None:
        for filename in ("theme.css", "workspace.css"):
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_name(filename)))
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build(self) -> None:
        # Keep GTK's native window actions, with close at the outer right edge.
        # Zero spacing also removes the gaps between the native title buttons.
        header = Gtk.HeaderBar(
            title="Peppermint", show_close_button=True, spacing=0,
            decoration_layout=":minimize,maximize,close",
        )
        self.header = header
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label="Peppermint")
        title.set_width_chars(14)
        title.get_style_context().add_class("title")
        self.page_title = Gtk.Label(label="Tasks")
        self.page_title.get_style_context().add_class("subtitle")
        title_box.pack_start(title, False, False, 0)
        title_box.pack_start(self.page_title, False, False, 0)
        header.set_custom_title(title_box)
        self.set_titlebar(header)

        self.menu_button = Gtk.ToggleButton()
        self.menu_button.set_tooltip_text("Open Peppermint sidebar")
        self.menu_button.get_accessible().set_name("Peppermint menu")
        self.menu_button.get_style_context().add_class("peppermint-menu-button")
        emblem = Gio.FileIcon.new(Gio.File.new_for_path(str(Path(__file__).with_name('assets') / 'peppermint-menu.svg')))
        menu_image = Gtk.Image.new_from_gicon(emblem, Gtk.IconSize.DIALOG)
        menu_image.set_pixel_size(36)
        self.menu_button.add(menu_image)
        self.main_menu = MainMenu(self.navigate, self.new_task, self.refresh, self._on_close, self.close_menu,
                                  self.open_recovery)
        self.status_dot = self.main_menu.status
        header.pack_start(self.menu_button)

        layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        layout.set_margin_start(24)
        layout.set_margin_end(24)
        layout.set_margin_top(24)
        layout.set_margin_bottom(16)
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)
        self.overlay.add(layout)
        self.workspace = layout

        self.menu_layer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.menu_layer.set_hexpand(True)
        self.menu_layer.set_vexpand(True)
        self.menu_revealer = Gtk.Revealer()
        self.menu_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.menu_revealer.set_transition_duration(280)
        self.menu_revealer.add(self.main_menu)
        self.menu_revealer.set_sensitive(False)
        self.menu_revealer.connect('notify::child-revealed', self._menu_transition_finished)
        self.menu_layer.pack_start(self.menu_revealer, False, False, 0)
        self.menu_shade = Gtk.EventBox()
        self.menu_shade.get_style_context().add_class('menu-shade')
        self.menu_shade.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.menu_shade.connect('button-press-event', self._outside_menu)
        self.menu_layer.pack_start(self.menu_shade, True, True, 0)
        self.overlay.add_overlay(self.menu_layer)
        self.menu_layer.show_all()
        self.menu_layer.set_no_show_all(True)
        self.menu_layer.hide()
        self.menu_button.connect('toggled', self._menu_toggled)

        workspace = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        layout.pack_start(workspace, True, True, 0)
        self.pages = Gtk.Stack()
        self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.pages.set_transition_duration(180)
        self.pages.set_hhomogeneous(False)
        self.pages.set_vhomogeneous(False)
        workspace.pack_start(self.pages, True, True, 0)

        self.task_board = TaskBoard(self._query_overview, self.open_task, self.open_diagnostics, self.new_task)
        self.pages.add_titled(self.task_board, "tasks", "Tasks")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.pages.add_titled(box, "conversations", "Conversations")
        self.diagnostics = self._diagnostics_view or DiagnosticsView()
        self.pages.add_titled(self.diagnostics, "diagnostics", "Diagnostics")
        self.manual = UserManual()
        self.pages.add_titled(self.manual, "manual", "User manual")
        self.pages.connect("notify::visible-child-name", self._sync_monitor)
        self.pages.connect("notify::visible-child-name", self._page_changed)

        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        composer.get_style_context().add_class("composer")
        box.pack_start(composer, False, False, 0)
        title = Gtk.Label(label="What would you like to solve?", xalign=0)
        title.set_line_wrap(True)
        title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.get_style_context().add_class("hero-title")
        composer.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(label="Explore solutions. Make a plan. Approve each action.", xalign=0)
        subtitle.set_line_wrap(True)
        subtitle.get_style_context().add_class("muted")
        composer.pack_start(subtitle, False, False, 0)

        entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        entry_box.set_margin_top(6)
        composer.pack_start(entry_box, False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_width_chars(8)
        self.entry.set_placeholder_text("Describe an issue or something you want to do…")
        self.entry.get_style_context().add_class("idea-entry")
        self.entry.connect("activate", self._on_submit)
        entry_box.pack_start(self.entry, True, True, 0)
        send = Gtk.Button(label="New conversation")
        send.get_style_context().add_class("suggested-action")
        send.connect("clicked", self._on_submit)
        entry_box.pack_start(send, False, False, 0)

        section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        section.get_style_context().add_class("section-rule")
        label = Gtk.Label(label="CONVERSATIONS", xalign=0)
        label.get_style_context().add_class("section-title")
        section.pack_start(label, True, True, 0)
        self.conversation_count = Gtk.Label(label="0 saved locally")
        self.conversation_count.get_style_context().add_class("muted")
        section.pack_start(self.conversation_count, False, False, 0)
        box.pack_start(section, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.pack_start(scroller, True, True, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", lambda _lb, row: row.toggle())
        self.list.set_placeholder(self._placeholder())
        scroller.add(self.list)

        footer = Gtk.Label(label="Runs locally  ·  You control actions and monitoring", xalign=0)
        footer.set_line_wrap(True)
        footer.get_style_context().add_class("muted")
        footer.get_style_context().add_class("footer")
        workspace.pack_start(footer, False, False, 0)
        # Stack destinations must be visible before OpenTask can select them,
        # including when the application was started in the background.
        self.pages.show_all()

    def _sync_monitor(self, *_):
        self.diagnostics.set_active(self.get_mapped() and self.pages.get_visible_child_name() == "diagnostics")

    def new_task(self):
        self.navigate("conversations")
        self.entry.grab_focus()

    def navigate(self, page: str):
        self.close_menu()
        self.pages.set_visible_child_name(page)
        if page == 'manual':
            self.manual.search.grab_focus()
        else:
            self.menu_button.grab_focus()

    def close_menu(self):
        self.menu_button.set_active(False)

    def open_recovery(self):
        from peppermint.cli import cmd_recover
        cmd_recover(None)

    def _menu_toggled(self, button):
        opened = button.get_active()
        self.workspace.set_sensitive(not opened)
        self.menu_revealer.set_sensitive(opened)
        if opened:
            self.menu_layer.show()
        self.menu_revealer.set_reveal_child(opened)
        if opened:
            self.main_menu.close_button.grab_focus()
        else:
            self.menu_button.grab_focus()
            self._menu_transition_finished()
        button.set_tooltip_text('Close Peppermint sidebar' if opened else 'Open Peppermint sidebar')

    def _menu_transition_finished(self, *_):
        if not self.menu_button.get_active() and not self.menu_revealer.get_child_revealed():
            self.menu_layer.hide()

    def _outside_menu(self, *_):
        self.close_menu()
        return True

    def _page_changed(self, *_):
        page = self.pages.get_visible_child_name()
        self.main_menu.set_page(page)
        self.page_title.set_text(next((title for name, title, _ in PAGES if name == page), 'Peppermint'))

    def _placeholder(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        title = Gtk.Label(label="A calmer way to work with your computer", xalign=0)
        title.set_line_wrap(True)
        title.get_style_context().add_class("task-title")
        box.pack_start(title, False, False, 0)
        hint = Gtk.Label(label="Start with an idea, or try one of these:", xalign=0)
        hint.set_line_wrap(True)
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
        self.new_task()
        self.entry.set_text(prompt)
        self.entry.grab_focus()
        self.entry.set_position(-1)

    # --- the daemon --------------------------------------------------------

    def _subscribe(self) -> None:
        self._bus = dbus_api.session_bus()
        self._subscription = self._bus.signal_subscribe(
            None, dbus_api.DAEMON_IFACE, "TaskUpdated", dbus_api.DAEMON_PATH,
            None, 0, self._on_task_updated,
        )

    def _on_task_updated(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        task_id, _status = params.unpack()
        GLib.idle_add(self._refresh_one, int(task_id))

    def _refresh_one(self, task_id: int) -> bool:
        if self._destroyed:
            return GLib.SOURCE_REMOVE
        self.refresh(details=False)
        if task_id in self.rows or task_id == self._diagnostic_task_id:
            self.request_detail(task_id)
        return GLib.SOURCE_REMOVE

    def refresh(self, *, details=True) -> None:
        if self._destroyed:
            return
        self.task_board.reload()
        self._reader.request("conversations", "ListTasks", GLib.Variant("(i)", (40,)), self._accept_tasks)
        if details:
            for task_id, row in tuple(self.rows.items()):
                if row.expanded:
                    self.request_detail(task_id)
            if self._diagnostic_task_id is not None:
                self.request_detail(self._diagnostic_task_id)

    def _query_overview(self, status_filter, query, offset):
        requested = (status_filter, query, offset)
        def accept(report, error):
            current = (self.task_board.filter.get_active_id(), self.task_board.search.get_text(), self.task_board.offset)
            if requested == current:
                self._accept_overview(report, error)
        self._reader.request("overview", "TaskOverview", GLib.Variant("(ssii)", (status_filter, query, offset, 40)),
                             accept)

    def _accept_overview(self, report, error):
        if self._destroyed:
            return
        if error:
            self.status_dot.set_text("● Offline" if isinstance(error, dbus_api.DaemonNotRunning) else "● Read error")
            self.task_board.show_error(error)
        else:
            self.status_dot.set_text("● Connected")
            self.task_board.update_report(report)

    def _accept_tasks(self, tasks, error):
        if self._destroyed:
            return
        if error:
            self.status_dot.set_text("● Offline" if isinstance(error, dbus_api.DaemonNotRunning) else "● Read error")
            return
        self.status_dot.set_text("● Connected")
        for index, task in enumerate(tasks):
            task_id = int(task["id"])
            row = self.rows.get(task_id)
            if row is None:
                row = TaskRow(task, self)
                self.rows[task_id] = row
                self.list.insert(row, index)
                row.show_all()
                if row.expanded:
                    self.request_detail(task_id)
            elif not row.expanded:
                row.update(task)
                if row.expanded:
                    self.request_detail(task_id)
            elif row._status != task.get("status"):
                self.request_detail(task_id)
        # Keep open conversations and drafts beyond the newest page. Inactive
        # rows can be loaded again from Tasks without retaining every widget.
        seen = {int(task['id']) for task in tasks}
        for task_id, row in tuple(self.rows.items()):
            if (task_id not in seen and task_id != self._opening_task_id and not row.expanded
                    and not row._chat_draft and not row._answer_draft):
                self.rows.pop(task_id)
                row.destroy()
        self.conversation_count.set_text(f"{len(self.rows)} loaded · All tasks in Tasks")

    def request_detail(self, task_id: int) -> None:
        self._reader.request(f"task:{task_id}", "GetTask", GLib.Variant("(i)", (task_id,)), self._accept_detail)

    def _accept_detail(self, task, error):
        if self._destroyed:
            return
        if error or not task:
            self.status_dot.set_text("● Detail unavailable")
            return
        task_id = int(task["id"])
        row = self.rows.get(task_id)
        if row is None and task_id == self._opening_task_id:
            row = TaskRow(task, self)
            self.rows[task_id] = row
            self.list.insert(row, 0)
            row.show_all()
            self.conversation_count.set_text(f"{len(self.rows)} loaded · All tasks in Tasks")
        if row:
            if task_id == self._opening_task_id:
                row.expanded = True
                row.arrow.set_label("▾")
                row.revealer.set_reveal_child(True)
                self._opening_task_id = None
                GLib.idle_add(self._scroll_to_task, task_id)
            row.update(task)
        if task_id == self._diagnostic_task_id:
            self.diagnostics.set_task_context(task)

    def _scroll_to_task(self, task_id):
        if not self._destroyed and task_id in self.rows:
            row = self.rows[task_id]
            adjustment = self.list.get_adjustment()
            if adjustment:
                adjustment.set_value(row.get_allocation().y)
        return GLib.SOURCE_REMOVE

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

    def retest(self, task_id: int, request_id: str, outcome: str) -> None:
        dbus_api.call_daemon("Retest", GLib.Variant("(iss)", (task_id, request_id, outcome)), timeout=10000)

    def chat(self, task_id: int, text: str) -> None:
        dbus_api.call_daemon("Chat", GLib.Variant("(is)", (task_id, text)), timeout=10000)
        self.request_detail(task_id)

    def open_task(self, task_id: int) -> None:
        self.pages.set_visible_child_name("conversations")
        self._opening_task_id = task_id
        self.request_detail(task_id)

    def open_diagnostics(self, task_id: int) -> None:
        self._diagnostic_task_id = task_id
        self.diagnostics.set_task_context(None)
        self.pages.set_visible_child_name("diagnostics")
        self.request_detail(task_id)

    def _error_dialog(self, message: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK, text="Peppermint cannot reach the daemon")
        dialog.format_secondary_text(message)
        dialog.get_style_context().add_class("peppermint-window")
        dialog.run()
        dialog.destroy()

    # --- window behaviour --------------------------------------------------

    def _on_destroy(self, *_):
        self._destroyed = True
        self.diagnostics.set_active(False)
        self._reader.close()
        if self._subscription:
            self._bus.signal_unsubscribe(self._subscription)

    def _on_close(self, *_args) -> bool:
        """The close button hides the window. The daemon keeps working."""
        self.close_menu()
        self.hide()
        return True

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.menu_button.get_active():
                self.close_menu()
                return True
            self.hide()
            return True
        return False
