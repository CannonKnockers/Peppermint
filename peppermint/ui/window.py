"""Tasks, conversations and opt-in system diagnostics in one local workspace."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from peppermint.common import dbus_api  # noqa: E402
from peppermint.ui.main_menu import MainMenu, PAGES  # noqa: E402
from peppermint.ui.manual import UserManual  # noqa: E402
from peppermint.ui.secret_entry import configure_secret_entry
from peppermint.ui.task_row import TaskRow  # noqa: E402
from peppermint.ui.plugins_view import PluginsView
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
        self._schedule_dialog = None
        self._diagnostic_task_id = None
        self._opening_task_id = None
        self._diagnostics_view = diagnostics
        self._recovery_panel = None
        self._active_task_id = None
        self._sidebar_rows: dict[int, TaskRow] = {}

        from peppermint import config
        from peppermint.diagnostics.tracking import ProcessTracking
        self.tracking = ProcessTracking(config.DATA_DIR / "tracking-reports")
        self._reports_view = None
        self._load_css()
        self._build()
        self._subscribe()
        self.connect("delete-event", self._on_close)
        self.connect("key-press-event", self._on_key)
        self.diagnostics.set_active(True)
        if hasattr(self.diagnostics, "on_track"):
            self.diagnostics.on_track = self._begin_tracking
            self.diagnostics.on_sample = self._track_sample
            self.connect("map", self._sync_diagnostic_render)
            self.connect("unmap", self._sync_diagnostic_render)
            self.pages.connect("notify::visible-child-name", self._sync_diagnostic_render)
            self._sync_diagnostic_render()
        self._refresh_tracking_menu()
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
        self.overlay = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(self.overlay)
        self.workspace = layout

        self.menu_layer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.menu_layer.set_hexpand(False)
        self.menu_layer.set_vexpand(True)
        self.menu_revealer = Gtk.Revealer()
        self.menu_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.menu_revealer.set_transition_duration(280)
        self.menu_revealer.add(self.main_menu)
        self.menu_revealer.set_sensitive(False)
        self.menu_revealer.connect('notify::child-revealed', self._menu_transition_finished)
        self.menu_layer.pack_start(self.menu_revealer, False, False, 0)
        self.overlay.pack_start(self.menu_layer, False, False, 0)
        page_scroll = Gtk.ScrolledWindow()
        page_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        page_scroll.add(layout)
        self.overlay.pack_start(page_scroll, True, True, 0)
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

        self.task_board = TaskBoard(self._query_overview, self.open_task, self.open_diagnostics, self.new_task, self.open_schedule)
        self.pages.add_titled(self.task_board, "tasks", "Tasks")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.pages.add_titled(box, "conversations", "Conversations")
        self.diagnostics = self._diagnostics_view or DiagnosticsView()
        self.pages.add_titled(self.diagnostics, "diagnostics", "Diagnostics")
        self.plugins_view = PluginsView()
        self.pages.add_titled(self.plugins_view, "plugins", "Plugins")
        self.manual = UserManual()
        self.pages.add_titled(self.manual, "manual", "User manual")
        self.pages.connect("notify::visible-child-name", self._page_changed)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.set_size_request(180, -1)
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.get_style_context().add_class("section-rule")
        label = Gtk.Label(label="CONVERSATIONS", xalign=0)
        label.get_style_context().add_class("section-title")
        section.pack_start(label, True, True, 0)
        self.conversation_count = Gtk.Label(label="0 saved locally")
        self.conversation_count.get_style_context().add_class("muted")
        section.pack_start(self.conversation_count, False, False, 0)
        left.pack_start(section, False, False, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", self._on_conversation_selected)
        self.list.connect("row-activated", lambda _list, row: self.open_task(row.task_id))
        self.list.set_placeholder(Gtk.Label(label="No conversations yet"))
        left.pack_start(self.list, False, False, 0)
        self.list.get_style_context().add_class("conversation-list")
        self.main_menu.history.pack_start(left, False, False, 0)
        left.show_all()

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.set_margin_start(10)
        right.set_margin_end(10)
        self.conversation_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.conversation_title = Gtk.Label(label="Select a conversation", xalign=0)
        self.conversation_title.get_style_context().add_class("task-title")
        self.conversation_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.conversation_title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.conversation_header.pack_start(self.conversation_title, True, True, 0)
        self.conversation_status = Gtk.Label(label="", xalign=1)
        self.conversation_status.get_style_context().add_class("muted")
        self.conversation_header.pack_start(self.conversation_status, False, False, 0)
        right.pack_start(self.conversation_header, False, False, 0)

        conversation_scroller = Gtk.ScrolledWindow()
        conversation_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.conversation_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        conversation_scroller.add(self.conversation_panel)
        right.pack_start(conversation_scroller, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.get_style_context().add_class("conversation-composer")
        self.conversation_entry = Gtk.Entry()
        self.conversation_entry.set_placeholder_text("Send a follow-up to the selected conversation…")
        self.conversation_entry.set_width_chars(8)
        self.conversation_entry.connect("activate", self._on_active_chat)
        self.conversation_entry.connect("changed", self._on_active_chat_changed)
        self.conversation_send = Gtk.Button(label="Send")
        self.conversation_send.get_style_context().add_class("suggested-action")
        self.conversation_send.connect("clicked", lambda *_: self._on_active_chat(None))
        footer.pack_start(self.conversation_entry, True, True, 0)
        footer.pack_start(self.conversation_send, False, False, 0)
        self.chat_controls = footer
        right.pack_start(footer, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_width_chars(8)
        self.entry.set_placeholder_text("Ask Peppermint…")
        self.entry.connect("activate", self._on_submit)
        footer.pack_start(self.entry, True, True, 0)
        footer.reorder_child(self.entry, 0)
        self.entry.set_no_show_all(True)
        self.conversation_entry.set_no_show_all(True)
        self.new_task()
        box.pack_start(right, True, True, 0)

        footer = Gtk.Label(label="Runs locally  ·  You control actions and monitoring", xalign=0)
        footer.set_line_wrap(True)
        footer.get_style_context().add_class("muted")
        footer.get_style_context().add_class("footer")
        workspace.pack_start(footer, False, False, 0)
        # Stack destinations must be visible before OpenTask can select them,
        # including when the application was started in the background.
        self.pages.show_all()
        self.pages.set_visible_child_name("conversations")

    def new_task(self):
        self.navigate("conversations")
        self._active_task_id = None
        self._opening_task_id = None
        self.list.unselect_all()
        self._set_conversation_placeholder()
        self.conversation_title.set_text("What would you like to solve?")
        self.conversation_status.set_text("")
        self._sync_composer()
        self.entry.grab_focus()

    def navigate(self, page: str):
        self.pages.set_visible_child_name(page)
        if page == 'manual':
            self.manual.search.grab_focus()
        else:
            self.menu_button.grab_focus()

    def close_menu(self):
        self.menu_button.set_active(False)

    def _sync_diagnostic_render(self, *_):
        self.diagnostics.set_render_visible(self.get_mapped() and self.pages.get_visible_child_name() == "diagnostics")

    def _begin_tracking(self, process):
        try:
            self.tracking.begin(process)
        except ValueError as exc:
            self.diagnostics.process_details.set_text(str(exc))
            return
        self.diagnostics.start_monitoring()
        self._sync_tracking_targets()
        self.diagnostics.process_details.set_text("Tracking in the background. Open Tracking reports in the sidebar to view graphs.")
        self._refresh_tracking_menu()

    def _sync_tracking_targets(self):
        monitor = getattr(self.diagnostics, "_monitor", None)
        sampler = getattr(monitor, "sampler", None)
        if sampler is not None:
            sampler.tracked_identities = self.tracking.identities

    def _track_sample(self, sample):
        self.tracking.ingest(sample)
        if self._reports_view is not None and self._reports_view.get_mapped():
            self._reports_view.refresh()

    def _refresh_tracking_menu(self):
        for child in self.main_menu.reports.get_children():
            child.destroy()
        for rid, report in sorted(self.tracking.reports.items(), reverse=True):
            button = Gtk.Button(label=f"{'● ' if report['active'] else ''}{report['name'][:25]} · {report['pid']}")
            button.get_style_context().add_class("menu-item")
            button.connect("clicked", lambda _button, key=rid: self._open_tracking_report(key))
            self.main_menu.reports.pack_start(button, False, False, 0)
        self.main_menu.reports.show_all()

    def _open_tracking_report(self, rid):
        if self._reports_view is None:
            from peppermint.ui.tracking_reports import TrackingReports
            self._reports_view = TrackingReports(self.tracking, self._stop_tracking)
            self.pages.add_named(self._reports_view, "tracking")
        self._reports_view.open_report(rid)
        self.navigate("tracking")

    def _stop_tracking(self, rid):
        try:
            self.tracking.stop(rid)
        except OSError as exc:
            self._reports_view.heading.set_text(f"Could not save report: {exc}")
            return
        self._sync_tracking_targets()
        self._refresh_tracking_menu()
        self._reports_view.refresh()

    def open_recovery(self):
        if self._recovery_panel is None:
            from peppermint.recovery.app import RecoveryPanel
            self._recovery_panel = RecoveryPanel(self, lambda: self.navigate("conversations"))
            self.pages.add_named(self._recovery_panel, "recovery")
            self._recovery_panel.show_all()
        else:
            self._recovery_panel.refresh()
        self.navigate("recovery")

    def _menu_toggled(self, button):
        opened = button.get_active()
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

    def _page_changed(self, *_):
        page = self.pages.get_visible_child_name()
        if page == "plugins":
            self.plugins_view.reload()
        self.main_menu.set_page(page)
        self.page_title.set_text(next((title for name, title, _ in PAGES if name == page), {'recovery': 'Recovery', 'tracking': 'Tracking report'}.get(page, 'Peppermint')))

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
        if task_id == self._active_task_id or task_id == self._diagnostic_task_id:
            self.request_detail(task_id)
        return GLib.SOURCE_REMOVE

    def refresh(self, *, details=True) -> None:
        if self._destroyed:
            return
        self.task_board.reload()
        if self.pages.get_visible_child_name() == "plugins":
            self.plugins_view.reload()
        self._reader.request("conversations", "ListTasks", GLib.Variant("(i)", (40,)), self._accept_tasks)
        if details:
            if self._active_task_id is not None:
                self.request_detail(self._active_task_id)
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
        for task in tasks:
            task_id = int(task["id"])
            sidebar = self._sidebar_rows.get(task_id)
            if sidebar is None:
                sidebar = TaskRow(task, self, compact=True)
                self._sidebar_rows[task_id] = sidebar
                self.list.add(sidebar)
                sidebar.show_all()
            else:
                sidebar.update(task)
            if task_id not in self.rows:
                self.rows[task_id] = TaskRow(task, self, external_composer=True)
            elif task_id == self._active_task_id:
                if self.rows[task_id]._status != task.get("status"):
                    self.request_detail(task_id)
        seen = {int(task['id']) for task in tasks}
        for task_id, row in tuple(self.rows.items()):
            if (task_id not in seen and task_id != self._active_task_id
                    and not row._chat_draft and not row._answer_draft):
                self.rows.pop(task_id).destroy()
                self._sidebar_rows.pop(task_id).destroy()
        self.conversation_count.set_text(f"{len(self.rows)} loaded · More in Tasks")

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
        if row is None and task_id == self._active_task_id:
            row = TaskRow(task, self, external_composer=True)
            self.rows[task_id] = row
            sidebar = TaskRow(task, self, compact=True)
            self._sidebar_rows[task_id] = sidebar
            self.list.insert(sidebar, 0)
            sidebar.show_all()
        if row:
            if task_id == self._active_task_id:
                row.expanded = True
                row.revealer.set_reveal_child(True)
            row.update(task)
            self._sidebar_rows[task_id].update(task)
            if task_id == self._active_task_id:
                self._opening_task_id = None
                self._show_conversation(row)
        if task_id == self._diagnostic_task_id:
            self.diagnostics.set_task_context(task)

    def _set_conversation_placeholder(self):
        for child in self.conversation_panel.get_children():
            self.conversation_panel.remove(child)
            if not isinstance(child, TaskRow):
                child.destroy()
        self.conversation_panel.pack_start(self._placeholder(), True, False, 0)

    def _on_conversation_selected(self, _list, row):
        if row is not None and row.task_id != self._active_task_id:
            self.open_task(row.task_id)

    def _show_conversation(self, row):
        if row.get_parent() is not self.conversation_panel:
            for child in self.conversation_panel.get_children():
                self.conversation_panel.remove(child)
                if not isinstance(child, TaskRow):
                    child.destroy()
            self.conversation_panel.pack_start(row, False, False, 0)
        row.show_all()
        self.conversation_title.set_text(row.idea.get_text())
        self.conversation_status.set_text(row.pill.get_text())
        self.list.select_row(self._sidebar_rows[row.task_id])
        self._sync_composer()

    def _sync_composer(self):
        row = self.rows.get(self._active_task_id)
        new = self._active_task_id is None
        self.entry.set_visible(new)
        self.conversation_entry.set_visible(not new)
        ready = new or (row is not None and row._status in ("done", "failed", "cancelled"))
        self.conversation_entry.set_sensitive(ready)
        self.conversation_send.set_sensitive(ready)
        configure_secret_entry(self.conversation_entry, row.reply_prompt if row else "")
        self.conversation_entry.set_text(row._chat_draft if row else "")
        self.conversation_entry.set_placeholder_text(
            "Ask a follow-up…" if ready else "Waiting for Peppermint — review any request above")

    def _on_active_chat_changed(self, entry):
        row = self.rows.get(self._active_task_id)
        if row is not None:
            row._chat_draft = entry.get_text()

    def _on_active_chat(self, *_):
        if self._active_task_id is None:
            self._on_submit()
            return
        row = self.rows.get(self._active_task_id)
        text = self.conversation_entry.get_text().strip()
        if row is None or row._status not in ("done", "failed", "cancelled") or not text:
            return
        self.conversation_entry.set_text("")
        try:
            self.chat(row.task_id, text)
        except Exception:
            self.conversation_entry.set_text(text)
            raise

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
        self._active_task_id = task_id
        self._opening_task_id = task_id
        row = self.rows.get(task_id)
        if row:
            row.expanded = True
            row.revealer.set_reveal_child(True)
            self._show_conversation(row)
        else:
            self._set_conversation_placeholder()
            self.conversation_title.set_text("Loading conversation…")
            self.conversation_status.set_text("")
            self._sync_composer()
        self.request_detail(task_id)

    def open_diagnostics(self, task_id: int) -> None:
        self._diagnostic_task_id = task_id
        self.diagnostics.set_task_context(None)
        self.pages.set_visible_child_name("diagnostics")
        self.request_detail(task_id)

    def open_schedule(self, task_id: int) -> None:
        from peppermint.ui.schedule_dialog import ScheduleDialog
        if self._schedule_dialog is not None:
            self._schedule_dialog.present()
            return
        self._schedule_dialog = ScheduleDialog(self, task_id, self.refresh)
        self._schedule_dialog.connect("destroy", self._schedule_closed)

    def _schedule_closed(self, *_):
        self._schedule_dialog = None

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
        if self._schedule_dialog is not None:
            self._schedule_dialog.destroy()
        try:
            self.tracking.close()
        except OSError:
            import logging
            logging.getLogger(__name__).exception("Could not save tracking reports")
        if self._recovery_panel is not None:
            self._recovery_panel._closed.set()
        self.diagnostics.set_active(False)
        self._reader.close()
        for row in self.rows.values():
            if row.get_parent() is None:
                row.destroy()
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
