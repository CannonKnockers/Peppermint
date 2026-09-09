"""Task schedule controls. All daemon calls run away from the GTK thread."""

import json
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from peppermint.common import dbus_api


class ScheduleDialog(Gtk.Dialog):
    def __init__(self, parent, task_id, on_changed, *, call=None):
        super().__init__(title="Task schedule", transient_for=parent, modal=True,
                         destroy_with_parent=True)
        self.get_style_context().add_class("peppermint-window")
        self.set_default_size(440, 360)
        self.task_id = task_id
        self._on_changed = on_changed
        self._call = call or dbus_api.call_daemon
        self._closed = False
        self.busy = False
        self.schedule = None
        self.ready = False
        self.connect("destroy", self._destroy)
        self.connect("delete-event", lambda *_: self.busy)
        self.connect("response", self._response)
        self.close_button = self.add_button("Close", Gtk.ResponseType.CLOSE)
        body = self.get_content_area()
        body.set_spacing(12)
        body.set_border_width(18)
        self.title_label = self._label(body, f"Task #{task_id}")
        self.title_label.get_style_context().add_class("task-title")
        self.state_label = self._label(body, "Loading schedule…")
        self.timing = Gtk.ComboBoxText.new_with_entry()
        for text in ("daily at 3pm", "every Monday", "every Friday at 09:30",
                     "every weekday at 9am", "every weekend", "hourly"):
            self.timing.append_text(text)
        self.entry = self.timing.get_child()
        self.entry.set_placeholder_text("For example: daily at 3pm")
        self.entry.set_max_length(120)
        self.entry.connect("changed", lambda *_: self._sync())
        self.entry.connect("activate", lambda *_: self._request("CreateSchedule") if self.add_button_control.get_sensitive() else None)
        body.pack_start(self.timing, False, False, 0)
        self._label(body, "Times use your local timezone. A day without a time means midnight.")
        self._label(body, "Each occurrence starts a new conversation with the original idea and asks for normal approvals. Unfinished work skips the next occurrence.")
        self._label(body, "Pause and Remove stop future occurrences. Existing tasks and history are kept. Use Stop to cancel work already queued.")
        actions = Gtk.Box(spacing=8)
        body.pack_start(actions, False, False, 0)
        self.add_button_control = Gtk.Button(label="Create schedule")
        self.add_button_control.get_style_context().add_class("suggested-action")
        self.toggle_button = Gtk.Button(label="Pause")
        self.remove_button = Gtk.Button(label="Remove schedule")
        for button in (self.add_button_control, self.toggle_button, self.remove_button):
            button.set_no_show_all(True)
            actions.pack_start(button, False, False, 0)
        self.add_button_control.connect("clicked", lambda *_: self._request("CreateSchedule"))
        self.toggle_button.connect("clicked", lambda *_: self._request(
            "PauseSchedule" if self.schedule and self.schedule.get("enabled") else "ResumeSchedule"))
        self.remove_button.connect("clicked", lambda *_: self._request("RemoveSchedule"))
        self.notice = self._label(body, "")
        self.retry = Gtk.Button(label="Reload schedule")
        self.retry.set_no_show_all(True)
        self.retry.connect("clicked", lambda *_: self._request())
        body.pack_start(self.retry, False, False, 0)
        self.show_all()
        self._request()

    @staticmethod
    def _label(body, text):
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        body.pack_start(label, False, False, 0)
        return label

    def _destroy(self, *_):
        self._closed = True

    def _response(self, *_):
        if not self.busy:
            self.destroy()

    def _sync(self):
        existing = bool(self.schedule)
        self.timing.set_visible(not existing)
        self.timing.set_sensitive(self.ready and not self.busy and not existing)
        self.add_button_control.set_visible(self.ready and not existing)
        self.add_button_control.set_sensitive(not self.busy and bool(self.entry.get_text().strip()))
        self.toggle_button.set_visible(self.ready and existing)
        self.toggle_button.set_label("Pause" if existing and self.schedule.get("enabled") else "Resume")
        self.toggle_button.set_sensitive(not self.busy)
        self.remove_button.set_visible(self.ready and existing)
        self.remove_button.set_sensitive(not self.busy)
        self.close_button.set_sensitive(not self.busy)
        self.retry.set_visible(not self.ready and not self.busy)

    def _request(self, method=None):
        if self.busy or self._closed or (method and not self.ready):
            return
        text = self.entry.get_text().strip()
        if method == "CreateSchedule" and not text:
            return
        self.busy = True
        self.notice.set_text("Saving schedule…" if method else "Loading schedule…")
        self._sync()

        def work():
            errors, warning, task = [], "", None
            if method:
                try:
                    params = GLib.Variant("(is)", (self.task_id, text)) if method == "CreateSchedule" else GLib.Variant("(i)", (self.task_id,))
                    result = self._call(method, params, timeout=120000)
                    if method == "CreateSchedule":
                        warning = json.loads(result.unpack()[0]).get("warning", "")
                except Exception as exc:
                    errors.append(str(exc))
            # Failures can leave a schedule paused. Always read the saved state.
            try:
                result = self._call("GetTask", GLib.Variant("(i)", (self.task_id,)),
                                    GLib.VariantType("(s)"), timeout=10000)
                task = json.loads(result.unpack()[0])
                if task is None:
                    errors.append("This task no longer exists.")
            except Exception as exc:
                errors.append("Could not reload the saved schedule: " + str(exc))
            GLib.idle_add(self._finish, task, errors, warning, method)

        try:
            threading.Thread(target=work, name="peppermint-schedule-controls", daemon=True).start()
        except Exception as exc:
            self.busy = False
            self.notice.set_text(str(exc))
            self._sync()

    def _finish(self, task, errors, warning, method):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.busy = False
        self.ready = task is not None
        if task is not None:
            self.schedule = task.get("schedule")
            self.title_label.set_text(f"Task #{self.task_id}: {task.get('idea', '')[:160]}")
            if self.schedule:
                state = "Enabled" if self.schedule.get("enabled") else "Paused"
                self.state_label.set_text(f"{state} · {self.schedule['schedule_text']}")
            else:
                self.state_label.set_text("No schedule")
        else:
            self.state_label.set_text("Schedule unavailable")
        self.notice.set_text("\n".join(errors + ([warning] if warning else [])) or ("Schedule saved." if method else ""))
        self._sync()
        if method:
            self._on_changed()
        return GLib.SOURCE_REMOVE
