"""A paginated view of persisted tasks, without replacing conversation widgets."""

from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from peppermint.ui.task_row import STATUS_CLASS


STATUS_LABELS = {
    "queued": "Queued", "planning": "Planning", "running": "Working",
    "awaiting-confirmation": "Needs approval", "awaiting-input": "Needs an answer",
    "done": "Completed", "failed": "Failed", "cancelled": "Stopped",
}


def updated_label(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return stamp.strftime("Updated %b %d · %H:%M")
    except (ValueError, TypeError, AttributeError):
        return ""


class TaskBoard(Gtk.Box):
    def __init__(self, on_query, on_open, on_diagnostics, on_new):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._on_query = on_query
        self._on_open = on_open
        self._on_diagnostics = on_diagnostics
        self._search_timer = None
        self.offset = 0
        self.limit = 40
        self.report = {}
        self.connect("destroy", self._destroy)

        heading = Gtk.Box(spacing=10)
        title = Gtk.Label(label="Your tasks", xalign=0)
        title.get_style_context().add_class("hero-title")
        heading.pack_start(title, True, True, 0)
        new = Gtk.Button(label="New task")
        new.get_style_context().add_class("suggested-action")
        new.connect("clicked", lambda *_: on_new())
        heading.pack_end(new, False, False, 0)
        self.pack_start(heading, False, False, 0)
        subtitle = Gtk.Label(label="Follow the work. Pick up where you left off.", xalign=0)
        subtitle.set_line_wrap(True)
        subtitle.get_style_context().add_class("muted")
        self.pack_start(subtitle, False, False, 0)

        summary = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        self.counters = {}
        for index, (key, caption) in enumerate((
            ("active", "Active"), ("waiting", "Needs you"),
            ("done", "Completed"), ("total", "All tasks"),
        )):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            card.get_style_context().add_class("task-summary")
            value = Gtk.Label(label="—", xalign=0)
            value.get_style_context().add_class("summary-number")
            label = Gtk.Label(label=caption, xalign=0)
            label.get_style_context().add_class("muted")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(9)
            label.set_tooltip_text(caption)
            card.pack_start(value, False, False, 0)
            card.pack_start(label, False, False, 0)
            summary.attach(card, index, 0, 1, 1)
            self.counters[key] = value
        self.pack_start(summary, False, False, 0)

        controls = Gtk.Box(spacing=8)
        self.filter = Gtk.ComboBoxText()
        for key, label in (("all", "All tasks"), ("active", "Active"), ("waiting", "Needs you"),
                           ("finished", "Finished"), ("failed", "Failed")):
            self.filter.append(key, label)
        self.filter.set_active_id("all")
        self.filter.connect("changed", self._changed)
        controls.pack_start(self.filter, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Search tasks…")
        self.search.set_width_chars(6)
        self.search.set_max_length(240)
        self.search.connect("search-changed", self._changed)
        controls.pack_start(self.search, True, True, 0)
        self.pack_start(controls, False, False, 0)
        self.notice = Gtk.Label(label="Loading saved tasks…", xalign=0)
        self.notice.set_line_wrap(True)
        self.notice.get_style_context().add_class("muted")
        self.pack_start(self.notice, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroller.add(self.cards)
        self.pack_start(scroller, True, True, 0)
        pagination = Gtk.Box(spacing=8)
        self.page_label = Gtk.Label(label="", xalign=0)
        self.page_label.get_style_context().add_class("muted")
        pagination.pack_start(self.page_label, True, True, 0)
        self.previous = Gtk.Button(label="Previous")
        self.next = Gtk.Button(label="Next")
        self.previous.connect("clicked", lambda *_: self._page(-1))
        self.next.connect("clicked", lambda *_: self._page(1))
        pagination.pack_end(self.next, False, False, 0)
        pagination.pack_end(self.previous, False, False, 0)
        self.previous.set_sensitive(False)
        self.next.set_sensitive(False)
        self.pack_start(pagination, False, False, 0)

    def _destroy(self, *_):
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = None

    def _changed(self, *_):
        self.offset = 0
        self.reload()

    def reload(self):
        # Debounce both typing and bursts of daemon state changes.
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(120, self._query)

    def _query(self):
        self._search_timer = None
        self._on_query(self.filter.get_active_id(), self.search.get_text(), self.offset)
        return GLib.SOURCE_REMOVE

    def _page(self, direction):
        self.offset = max(0, self.offset + direction * self.limit)
        self.reload()

    def show_error(self, error):
        self.notice.set_text("Task list unavailable. Check the daemon, then refresh.")
        self.notice.set_tooltip_text(str(error))
        self.previous.set_sensitive(False)
        self.next.set_sensitive(False)

    def update_report(self, report):
        self.report = report
        counts = report.get("counts", {})
        values = {
            "active": sum(counts.get(s, 0) for s in ("queued", "planning", "running")),
            "waiting": sum(counts.get(s, 0) for s in ("awaiting-confirmation", "awaiting-input")),
            "done": counts.get("done", 0), "total": report.get("total", 0),
        }
        for key, value in values.items():
            self.counters[key].set_text(str(value))
        self.offset = report.get("offset", 0)
        self.limit = report.get("limit", 40)
        tasks = report.get("tasks", [])
        matched = report.get("matched", 0)
        if self.offset and not tasks and matched:
            self.offset = ((matched - 1) // self.limit) * self.limit
            self.reload()
            return
        self.notice.set_text("Most recently updated first · Saved on this computer" if tasks else
                             "No matching tasks." if values["total"] else
                             "Your next idea starts here. Create a task to begin.")
        self.notice.set_tooltip_text(None)
        self.page_label.set_text(f"{self.offset + 1}–{self.offset + len(tasks)} of {matched}" if tasks else "0 results")
        self.previous.set_sensitive(self.offset > 0)
        self.next.set_sensitive(bool(report.get("has_more")))
        for child in self.cards.get_children():
            child.destroy()
        for task in tasks:
            self.cards.pack_start(self._card(task), False, False, 0)
        self.cards.show_all()

    def _card(self, task):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("overview-card")
        heading = Gtk.Box(spacing=8)
        title = Gtk.Label(label=task.get("idea", "").replace("\n", " "), xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_width_chars(8)
        title.set_tooltip_text(task.get("idea"))
        title.get_style_context().add_class("task-title")
        heading.pack_start(title, True, True, 0)
        if int(task.get("parent_task_id", 0) or 0):
            branch = Gtk.Label(label="⎇")
            branch.get_style_context().add_class("branch-icon")
            branch.set_tooltip_text("Forked from another task")
            heading.pack_start(branch, False, False, 0)
        schedule = task.get("schedule")
        if schedule:
            clock = Gtk.Image.new_from_icon_name("appointment-soon-symbolic", Gtk.IconSize.MENU)
            state = "enabled" if schedule.get("enabled") else "paused"
            clock.set_tooltip_text(f"{schedule['schedule_text']} · {state} · local time")
            heading.pack_start(clock, False, False, 0)
        status = task.get("status", "unknown")
        pill = Gtk.Label(label=STATUS_LABELS.get(status, status))
        pill.get_style_context().add_class("pill")
        pill.get_style_context().add_class(STATUS_CLASS.get(status, "pill-idle"))
        heading.pack_end(pill, False, False, 0)
        card.pack_start(heading, False, False, 0)
        plan = task.get("plan") or []
        detail = f"#{task['id']}"
        updated = updated_label(task.get("updated_at"))
        if updated:
            detail += " · " + updated
        if plan:
            done = sum(step.get("status") == "done" for step in plan)
            detail += f"\nChecklist: {done} of {len(plan)} recorded complete"
        label = Gtk.Label(label=detail, xalign=0)
        label.set_line_wrap(True)
        label.get_style_context().add_class("muted")
        card.pack_start(label, False, False, 0)
        actions = Gtk.Box(spacing=8)
        open_button = Gtk.Button(label="Review" if status.startswith("awaiting-") else "Open")
        open_button.get_style_context().add_class("suggested-action" if status.startswith("awaiting-") else "task-link")
        open_button.connect("clicked", lambda *_: self._on_open(int(task["id"])))
        diagnostics = Gtk.Button(label="Diagnostics")
        diagnostics.get_style_context().add_class("task-link")
        diagnostics.set_tooltip_text("Open system measurements alongside this task")
        diagnostics.connect("clicked", lambda *_: self._on_diagnostics(int(task["id"])))
        actions.pack_start(open_button, False, False, 0)
        actions.pack_start(diagnostics, False, False, 0)
        card.pack_start(actions, False, False, 0)
        return card
