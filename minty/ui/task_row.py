"""One row in the task list."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

STATUS_TEXT = {
    "queued": "waiting",
    "planning": "thinking",
    "running": "working",
    "awaiting-confirmation": "needs your approval",
    "awaiting-input": "has a question",
    "done": "done",
    "failed": "failed",
    "cancelled": "stopped",
}

STATUS_CLASS = {
    "queued": "pill-idle",
    "planning": "pill-busy",
    "running": "pill-busy",
    "awaiting-confirmation": "pill-wait",
    "awaiting-input": "pill-wait",
    "done": "pill-ok",
    "failed": "pill-bad",
    "cancelled": "pill-idle",
}

STEP_MARK = {"ok": "✓", "error": "!", "pending": "…", "denied": "✕", "asked": "?"}


class TaskRow(Gtk.ListBoxRow):
    """Shows one task. Expands to show the steps and any question."""

    def __init__(self, task: dict, client):
        super().__init__()
        self.task_id = int(task["id"])
        self.client = client
        self.expanded = False
        self._status = ""
        # ListTasks sends no steps. Keep the last full detail, so a list
        # refresh never wipes an open step log.
        self._steps: list[dict] = []

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(2)
        outer.set_margin_bottom(2)
        self.add(outer)

        # --- header ---------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(10)
        header.set_margin_end(10)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        outer.pack_start(header, False, False, 0)

        self.arrow = Gtk.Label(label="▸")
        self.arrow.get_style_context().add_class("dim-label")
        header.pack_start(self.arrow, False, False, 0)

        self.idea = Gtk.Label(xalign=0)
        self.idea.set_ellipsize(Pango.EllipsizeMode.END)
        self.idea.set_line_wrap(False)
        header.pack_start(self.idea, True, True, 0)

        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner, False, False, 0)

        self.pill = Gtk.Label()
        self.pill.get_style_context().add_class("pill")
        header.pack_start(self.pill, False, False, 0)

        # --- detail ---------------------------------------------------------
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(120)
        outer.pack_start(self.revealer, False, False, 0)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.detail.set_margin_start(26)
        self.detail.set_margin_end(10)
        self.detail.set_margin_bottom(10)
        self.revealer.add(self.detail)

        self.update(task)

    # --- expansion ---------------------------------------------------------

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.arrow.set_label("▾" if self.expanded else "▸")
        self.revealer.set_reveal_child(self.expanded)
        if self.expanded:
            self.client.request_detail(self.task_id)

    def expand(self) -> None:
        if not self.expanded:
            self.toggle()

    # --- content -----------------------------------------------------------

    def update(self, task: dict) -> None:
        if task.get("steps"):
            self._steps = task["steps"]
        else:
            task = dict(task, steps=self._steps)

        self.idea.set_text(task["idea"].replace("\n", " "))
        self.idea.set_tooltip_text(task["idea"])

        status = task["status"]
        if status != self._status:
            context = self.pill.get_style_context()
            for name in set(STATUS_CLASS.values()):
                context.remove_class(name)
            context.add_class(STATUS_CLASS.get(status, "pill-idle"))
            self.pill.set_text(STATUS_TEXT.get(status, status))
            self._status = status

        if status in ("running", "planning", "queued"):
            self.spinner.start()
            self.spinner.show()
        else:
            self.spinner.stop()
            self.spinner.hide()

        if status in ("awaiting-confirmation", "awaiting-input"):
            self.expand()

        if self.expanded:
            self._build_detail(task)

    def _build_detail(self, task: dict) -> None:
        for child in self.detail.get_children():
            self.detail.remove(child)

        steps = task.get("steps") or []
        if steps:
            self.detail.pack_start(self._step_log(steps), False, False, 0)

        if task.get("pending") and task["status"] == "awaiting-confirmation":
            self.detail.pack_start(self._approval_bar(task["pending"]), False, False, 0)

        if task["status"] == "awaiting-input" and task.get("question"):
            self.detail.pack_start(self._question_box(task["question"]), False, False, 0)

        if task.get("result"):
            self.detail.pack_start(self._text_block(task["result"], "result"), False, False, 0)

        if task.get("error"):
            self.detail.pack_start(self._text_block(task["error"], "error"), False, False, 0)

        if task["status"] in ("done", "failed"):
            self.detail.pack_start(self._chat_box(), False, False, 0)

        self.detail.show_all()

    def _step_log(self, steps: list[dict]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for step in steps:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            mark = Gtk.Label(label=STEP_MARK.get(step["status"], "-"), xalign=0)
            mark.get_style_context().add_class("step-" + step["status"])
            line.pack_start(mark, False, False, 0)

            summary = self._describe(step)
            label = Gtk.Label(label=summary, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.get_style_context().add_class("step-text")
            output = (step.get("output") or "").strip()
            if output:
                label.set_tooltip_text(output[:1200])
            line.pack_start(label, True, True, 0)
            box.pack_start(line, False, False, 0)
        return box

    @staticmethod
    def _describe(step: dict) -> str:
        tool = step["tool"]
        args = step.get("args") or {}
        if tool == "run_shell":
            return f"run: {args.get('cmd', '')}"
        if tool in ("read_file", "list_dir", "write_file", "delete_file"):
            return f"{tool.replace('_', ' ')}: {args.get('path', '')}"
        if tool == "move_file":
            return f"move: {args.get('src', '')} → {args.get('dst', '')}"
        if tool in ("gsettings_get", "gsettings_set"):
            value = f" = {args.get('value')}" if "value" in args else ""
            return f"{tool.replace('_', ' ')}: {args.get('schema', '')} {args.get('key', '')}{value}"
        if tool == "apt_install":
            packages = args.get("packages", [])
            return "install: " + (", ".join(packages) if isinstance(packages, list) else str(packages))
        if tool == "ask_user":
            return f"asked: {args.get('question', '')}"
        detail = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])
        return f"{tool.replace('_', ' ')}{': ' + detail if detail else ''}"

    def _approval_bar(self, pending: dict) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.get_style_context().add_class("approval")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        frame.add(box)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>Minty needs your approval</b>")
        box.pack_start(title, False, False, 0)

        what = Gtk.Label(label=pending["description"], xalign=0)
        what.set_line_wrap(True)
        what.set_selectable(True)
        what.get_style_context().add_class("mono")
        box.pack_start(what, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        deny = Gtk.Button(label="No")
        deny.connect("clicked", lambda *_: self.client.confirm(self.task_id, False))
        allow = Gtk.Button(label="Allow")
        allow.get_style_context().add_class("suggested-action")
        allow.connect("clicked", lambda *_: self.client.confirm(self.task_id, True))
        buttons.pack_start(deny, False, False, 0)
        buttons.pack_start(allow, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        return frame

    def _question_box(self, question: str) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.get_style_context().add_class("approval")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        frame.add(box)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>Minty asks:</b> {GLib.markup_escape_text(question)}")
        title.set_line_wrap(True)
        box.pack_start(title, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Your answer...")
        entry.connect("activate", self._on_answer)
        box.pack_start(entry, False, False, 0)
        entry.grab_focus()
        return frame

    def _on_answer(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text:
            self.client.answer(self.task_id, text)
            entry.set_text("")

    def _chat_box(self) -> Gtk.Widget:
        entry = Gtk.Entry()
        entry.set_placeholder_text("Ask Minty to continue...")
        entry.connect("activate", self._on_chat)
        return entry

    def _on_chat(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text:
            self.client.chat(self.task_id, text)
            entry.set_text("")

    @staticmethod
    def _text_block(text: str, kind: str) -> Gtk.Widget:
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_selectable(True)
        label.get_style_context().add_class(kind)
        return label
