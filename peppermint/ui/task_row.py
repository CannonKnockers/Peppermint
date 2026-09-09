"""One row in the task list."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from peppermint.common.retest import read_record

STATUS_TEXT = {
    "queued": "waiting",
    "planning": "Planning",
    "running": "Working",
    "awaiting-confirmation": "needs your approval",
    "awaiting-input": "has a question",
    "done": "Completed",
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

STEP_MARK = {"ok": "✓", "error": "!", "pending": "…", "denied": "✕", "asked": "?",
             "user_reported": "•"}


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
        self._messages: list[dict] = []
        self._plan: list[dict] = []
        self._retest = None
        self._chat_draft = ""
        self._answer_draft = ""
        self._chat_entry = None
        self._answer_entry = None
        self._detail_key = None
        self._activity_expanded = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_margin_bottom(12)
        outer.get_style_context().add_class("task-card")
        self.add(outer)

        # --- header ---------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(16)
        header.set_margin_end(16)
        header.set_margin_top(14)
        header.set_margin_bottom(14)
        outer.pack_start(header, False, False, 0)

        self.arrow = Gtk.Label(label="▸")
        self.arrow.get_style_context().add_class("dim-label")
        header.pack_start(self.arrow, False, False, 0)

        self.branch = Gtk.Label(label="⎇")
        self.branch.get_style_context().add_class("branch-icon")
        self.branch.set_tooltip_text("Forked from another task")
        self.branch.set_no_show_all(True)
        header.pack_start(self.branch, False, False, 0)

        self.idea = Gtk.Label(xalign=0)
        self.idea.set_ellipsize(Pango.EllipsizeMode.END)
        self.idea.set_line_wrap(False)
        self.idea.get_style_context().add_class("task-title")
        header.pack_start(self.idea, True, True, 0)

        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        header.pack_start(self.spinner, False, False, 0)

        self.pill = Gtk.Label()
        self.pill.get_style_context().add_class("pill")
        header.pack_start(self.pill, False, False, 0)

        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.get_style_context().add_class("stop-button")
        self.stop_button.set_tooltip_text("Stop this conversation's current task")
        self.stop_button.set_no_show_all(True)
        self.stop_button.connect("clicked", lambda *_: self.client.cancel(self.task_id))
        header.pack_end(self.stop_button, False, False, 0)

        # --- detail ---------------------------------------------------------
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(180)
        outer.pack_start(self.revealer, False, False, 0)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.detail.set_margin_start(16)
        self.detail.set_margin_end(16)
        self.detail.set_margin_bottom(16)
        self.detail.get_style_context().add_class("conversation")
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

        if task.get("messages"):
            self._messages = task["messages"]
        if "plan" in task:
            self._plan = task.get("plan") or []
        if "retest" in task:
            self._retest = task.get("retest")
        task = dict(task, messages=self._messages, plan=self._plan, retest=self._retest)

        self.idea.set_text(task["idea"].replace("\n", " "))
        self.idea.set_tooltip_text(task["idea"])
        self.branch.set_visible(bool(int(task.get("parent_task_id", 0) or 0)))

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

        self.stop_button.set_visible(status in (
            "queued", "planning", "running", "awaiting-confirmation", "awaiting-input"))

        if status in ("awaiting-confirmation", "awaiting-input") and not self.expanded:
            self.expanded = True
            self.arrow.set_label("▾")
            self.revealer.set_reveal_child(True)

        if self.expanded:
            key = repr(tuple(task.get(k) for k in (
                "idea", "status", "messages", "steps", "plan", "pending", "question", "retest", "result", "error")))
            if key != self._detail_key:
                self._build_detail(task)
                self._detail_key = key

    def _build_detail(self, task: dict) -> None:
        focused = None
        for kind, entry in (("chat", self._chat_entry), ("answer", self._answer_entry)):
            if entry is not None and entry.has_focus():
                focused = (kind, entry.get_position())
        self._chat_entry = None
        self._answer_entry = None
        for child in self.detail.get_children():
            self.detail.remove(child)

        messages = task.get("messages") or [{"role": "user", "content": task["idea"]}]
        for message in messages:
            self.detail.pack_start(self._message_block(message), False, False, 0)

        if task.get("plan"):
            self.detail.pack_start(self._plan_box(task["plan"]), False, False, 0)

        steps = task.get("steps") or []
        if steps:
            self.detail.pack_start(self._step_log(steps), False, False, 0)

        if task.get("pending") and task["status"] == "awaiting-confirmation":
            self.detail.pack_start(self._approval_bar(task["pending"]), False, False, 0)

        if task["status"] == "awaiting-input" and task.get("question"):
            self.detail.pack_start(self._question_box(task["question"], task.get("retest")), False, False, 0)

        if task.get("result") and not any(
                m.get("role") == "assistant" and m.get("content") == task["result"]
                for m in messages):
            self.detail.pack_start(self._message_block(
                {"role": "assistant", "content": task["result"]}), False, False, 0)

        if task.get("error"):
            self.detail.pack_start(self._text_block(task["error"], "error"), False, False, 0)

        if task["status"] in ("done", "failed", "cancelled"):
            self.detail.pack_start(self._chat_box(), False, False, 0)

        self.detail.show_all()
        if focused:
            entry = self._chat_entry if focused[0] == "chat" else self._answer_entry
            if entry is not None:
                entry.grab_focus()
                entry.set_position(focused[1])

    def _message_block(self, message: dict) -> Gtk.Widget:
        user = message.get("role") == "user"
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("message")
        box.get_style_context().add_class("message-user" if user else "message-assistant")
        speaker = Gtk.Label(label="YOU" if user else "PEPPERMINT", xalign=0)
        speaker.get_style_context().add_class("speaker")
        box.pack_start(speaker, False, False, 0)
        box.pack_start(self._text_block(message.get("content", ""), "result"), False, False, 0)
        return box

    def _plan_box(self, plan: list[dict]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label="PLAN", xalign=0)
        title.get_style_context().add_class("section-title")
        box.pack_start(title, False, False, 0)
        for item in plan:
            status = item.get("status", "pending")
            mark = {"done": "✓", "in_progress": "◉"}.get(status, "○")
            label = self._text_block(f"{mark}  {item.get('description', '')}",
                                     "step-ok" if status == "done" else "muted")
            box.pack_start(label, False, False, 0)
            if status == "done":
                # These fields are normalized by set_plan from the recorded
                # tool, rather than supplied by the model as outcome claims.
                scope = {"inspection": "Inspection completed",
                         "action": "Action completed",
                         "command": "Command completed",
                         "user_reported": "User-reported pass"}.get(item.get("evidence_kind"))
                evidence = (f"{scope} · {item['evidence_tool']} · step {item['evidence_step_id']}"
                            if scope and item.get("evidence_tool") and item.get("evidence_step_id")
                            else "Recorded completion · evidence scope unavailable")
                box.pack_start(self._text_block(evidence, "muted"), False, False, 0)
        return box

    def _step_log(self, steps: list[dict]) -> Gtk.Widget:
        expander = Gtk.Expander(label=f"Activity · {len(steps)} steps")
        expander.get_style_context().add_class("activity")
        expander.set_expanded(self._activity_expanded)
        expander.connect("notify::expanded", lambda widget, _prop:
                         setattr(self, "_activity_expanded", widget.get_expanded()))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(10)
        expander.add(box)
        for step in steps:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            mark = Gtk.Label(label=STEP_MARK.get(step["status"], "-"), xalign=0)
            mark.get_style_context().add_class("step-" + step["status"])
            line.pack_start(mark, False, False, 0)

            summary = self._describe(step)
            label = Gtk.Label(label=summary, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.get_style_context().add_class("step-text")
            line.pack_start(label, True, True, 0)
            box.pack_start(line, False, False, 0)
            output = (step.get("output") or "").strip()
            if output:
                label.set_tooltip_text(output)
                details = Gtk.Expander(label="Output")
                details.add(self._text_block(output, "mono"))
                box.pack_start(details, False, False, 0)
        return expander

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
        if tool == "request_retest":
            record = read_record(step.get('output', ''))
            outcome = (record or {}).get('outcome')
            if outcome:
                label = {'passed': 'passed', 'failed': 'still failing', 'not_tested': 'not tested'}[outcome]
                suffix = ' (superseded)' if step.get('status') == 'superseded' else ''
                return f"User-reported retest: {label}{suffix}"
            return f"Retest requested: {args.get('symptom', '')}"
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
        title.set_markup("<b>Permission required</b>")
        box.pack_start(title, False, False, 0)

        what = Gtk.Label(label=pending["description"], xalign=0)
        what.set_line_wrap(True)
        what.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        what.set_max_width_chars(76)
        what.set_selectable(True)
        what.get_style_context().add_class("mono")
        box.pack_start(what, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        deny = Gtk.Button(label="Deny")
        deny.get_style_context().add_class("deny-button")
        deny.connect("clicked", lambda *_: respond(False))
        allow = Gtk.Button(label="Allow once")
        allow.get_style_context().add_class("suggested-action")
        allow.connect("clicked", lambda *_: respond(True))
        def respond(approved):
            buttons.set_sensitive(False)
            try:
                if pending.get("id") is not None:
                    self.client.confirm(self.task_id, approved, pending["id"])
                else:
                    self.client.confirm(self.task_id, approved)
            except Exception:
                buttons.set_sensitive(True)
                raise

        buttons.pack_start(deny, False, False, 0)
        buttons.pack_start(allow, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        return frame

    def _question_box(self, question: str, retest: dict | None = None) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.get_style_context().add_class("approval")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        frame.add(box)

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>Peppermint asks:</b> {GLib.markup_escape_text(question)}")
        title.set_line_wrap(True)
        title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.set_max_width_chars(76)
        box.pack_start(title, False, False, 0)

        if retest:
            box.pack_start(self._text_block(
                "Choose the result of your test. This records your report; Peppermint "
                "has not independently verified the outcome.", "retest-note"), False, False, 0)
            choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            submitted = False

            def submit(outcome):
                nonlocal submitted
                if submitted:
                    return
                submitted = True
                choices.set_sensitive(False)
                try:
                    self.client.retest(self.task_id, retest['request_id'], outcome)
                except Exception:
                    submitted = False
                    choices.set_sensitive(True)
                    raise

            for outcome, label in (('passed', 'Passed'), ('failed', 'Still failing'),
                                   ('not_tested', 'Not tested')):
                button = Gtk.Button(label=label)
                button.get_style_context().add_class("choice")
                button.connect("clicked", lambda _button, value=outcome: submit(value))
                choices.pack_start(button, False, False, 0)
            box.pack_start(choices, False, False, 0)
        else:
            latest = next((s for s in reversed(self._steps) if s["tool"] == "ask_user"), {})
            for option in latest.get("args", {}).get("options", []):
                button = Gtk.Button(label=option)
                button.get_style_context().add_class("choice")
                button.get_child().set_line_wrap(True)
                button.get_child().set_max_width_chars(70)
                button.connect("clicked", lambda _button, value=option: self.client.answer(self.task_id, value))
                box.pack_start(button, False, False, 0)

        entry = Gtk.Entry()
        self._answer_entry = entry
        entry.set_placeholder_text("Add details without recording a test result…" if retest else "Your answer…")
        entry.set_text(self._answer_draft)
        entry.connect("changed", lambda widget: setattr(self, "_answer_draft", widget.get_text()))
        entry.connect("activate", self._on_answer)
        answer_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        answer_row.pack_start(entry, True, True, 0)
        send = Gtk.Button(label="Reply")
        send.get_style_context().add_class("suggested-action")
        send.connect("clicked", lambda *_: self._on_answer(entry))
        answer_row.pack_start(send, False, False, 0)
        box.pack_start(answer_row, False, False, 0)
        return frame

    def _on_answer(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text:
            entry.set_text("")
            try:
                self.client.answer(self.task_id, text)
            except Exception:
                entry.set_text(text)
                raise

    def _chat_box(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hint = Gtk.Label(label="Continue this conversation", xalign=0)
        hint.get_style_context().add_class("muted")
        box.pack_start(hint, False, False, 0)
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        entry = Gtk.Entry()
        self._chat_entry = entry
        entry.set_placeholder_text("Ask a follow-up or describe the next step…")
        entry.set_text(self._chat_draft)
        entry.connect("changed", lambda widget: setattr(self, "_chat_draft", widget.get_text()))
        entry.connect("activate", self._on_chat)
        controls.pack_start(entry, True, True, 0)
        send = Gtk.Button(label="Send")
        send.get_style_context().add_class("suggested-action")
        send.connect("clicked", lambda *_: self._on_chat(entry))
        controls.pack_start(send, False, False, 0)
        box.pack_start(controls, False, False, 0)
        return box

    def _on_chat(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text:
            entry.set_text("")
            try:
                self.client.chat(self.task_id, text)
            except Exception:
                entry.set_text(text)
                if self._chat_entry is not None:
                    self._chat_entry.set_text(text)
                raise

    @staticmethod
    def _text_block(text: str, kind: str) -> Gtk.Widget:
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(80)
        label.set_selectable(True)
        label.get_style_context().add_class(kind)
        return label
