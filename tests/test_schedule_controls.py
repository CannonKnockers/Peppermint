"""GTK scheduling actions use the existing API without touching real timers."""
import json
import threading
import time
from unittest.mock import Mock

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from peppermint.ui.schedule_dialog import ScheduleDialog
from peppermint.ui.task_board import TaskBoard


def wait_for(predicate):
    deadline = time.monotonic() + 3
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def make_dialog():
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')
    dialogs = []
    def make(call):
        changed = Mock()
        dialog = ScheduleDialog(None, 7, changed, call=call)
        dialogs.append(dialog)
        return dialog, changed
    yield make
    for dialog in dialogs:
        dialog.destroy()


class Bus:
    def __init__(self):
        self.schedule = None
        self.calls = []
        self.fail = None
        self.warning = ''

    def __call__(self, method, params, reply_type=None, **kwargs):
        args = params.unpack()
        self.calls.append((method, args, threading.get_ident()))
        assert args[0] == 7
        if method == 'GetTask':
            return GLib.Variant('(s)', (json.dumps(dict(id=7, idea='Check disk space', schedule=self.schedule)),))
        if method == 'CreateSchedule':
            if args[1] == 'tomorrow':
                raise ValueError('Unsupported schedule. Try every Monday.')
            self.schedule = dict(schedule_text=args[1], enabled=True)
        elif method == 'PauseSchedule':
            self.schedule['enabled'] = False
        elif method == 'ResumeSchedule':
            self.schedule['enabled'] = True
        elif method == 'RemoveSchedule':
            self.schedule = None
        else:
            raise AssertionError(method)
        if self.fail:
            if self.schedule:
                self.schedule['enabled'] = False
            raise RuntimeError(self.fail)
        return GLib.Variant('(s)', (json.dumps(dict(warning=self.warning)),)) if method == 'CreateSchedule' else None


def test_create_pause_resume_remove_and_refresh(make_dialog):
    bus = Bus()
    view, changed = make_dialog(bus)
    wait_for(lambda: not view.busy)
    assert view.state_label.get_text() == 'No schedule'
    assert not view.add_button_control.get_sensitive()
    view.entry.set_text('daily at 3pm')
    view.add_button_control.clicked()
    wait_for(lambda: not view.busy)
    assert view.toggle_button.get_label() == 'Pause'
    assert 'daily at 3pm' in view.state_label.get_text()
    assert not view.add_button_control.get_visible()
    view.toggle_button.clicked()
    wait_for(lambda: not view.busy)
    assert view.toggle_button.get_label() == 'Resume'
    assert 'Paused' in view.state_label.get_text()
    view.toggle_button.clicked()
    wait_for(lambda: not view.busy)
    assert view.toggle_button.get_label() == 'Pause'
    view.remove_button.clicked()
    wait_for(lambda: not view.busy)
    assert view.add_button_control.get_visible()
    assert not view.remove_button.get_visible()
    assert changed.call_count == 4
    assert [name for name, _, _ in bus.calls] == [
        'GetTask', 'CreateSchedule', 'GetTask', 'PauseSchedule', 'GetTask',
        'ResumeSchedule', 'GetTask', 'RemoveSchedule', 'GetTask']
    assert all(thread_id != threading.get_ident() for _, _, thread_id in bus.calls)


def test_invalid_phrase_keeps_input(make_dialog):
    bus = Bus()
    view, _ = make_dialog(bus)
    wait_for(lambda: not view.busy)
    view.entry.set_text('tomorrow')
    view.add_button_control.clicked()
    wait_for(lambda: not view.busy)
    assert 'Unsupported schedule' in view.notice.get_text()
    assert view.entry.get_text() == 'tomorrow'
    assert view.add_button_control.get_sensitive()
    assert view.state_label.get_text() == 'No schedule'


def test_partial_failure_shows_saved_paused_state(make_dialog):
    bus = Bus()
    bus.fail = 'Schedule saved paused: systemctl failed'
    view, changed = make_dialog(bus)
    wait_for(lambda: not view.busy)
    view.entry.set_text('hourly')
    view.add_button_control.clicked()
    wait_for(lambda: not view.busy)
    assert 'systemctl failed' in view.notice.get_text()
    assert view.toggle_button.get_label() == 'Resume'
    assert 'Paused' in view.state_label.get_text()
    changed.assert_called_once()


def test_cron_warning_stays_visible(make_dialog):
    bus = Bus()
    bus.warning = 'Systemd unavailable. Using cron.'
    view, _ = make_dialog(bus)
    wait_for(lambda: not view.busy)
    view.entry.set_text('hourly')
    view.add_button_control.clicked()
    wait_for(lambda: not view.busy)
    assert 'Using cron' in view.notice.get_text()


def test_offline_then_reload(make_dialog):
    bus = Bus()
    offline = True
    def call(*args, **kwargs):
        if offline:
            raise RuntimeError('Daemon unavailable')
        return bus(*args, **kwargs)
    view, _ = make_dialog(call)
    wait_for(lambda: not view.busy)
    assert view.retry.get_visible()
    assert not view.add_button_control.get_visible()
    assert 'Daemon unavailable' in view.notice.get_text()
    offline = False
    view.retry.clicked()
    wait_for(lambda: not view.busy)
    assert view.ready
    assert not view.retry.get_visible()


def test_slow_request_blocks_duplicate_writes_but_not_gtk(make_dialog):
    bus = Bus()
    release = threading.Event()
    entered = threading.Event()
    def call(method, *args, **kwargs):
        if method == 'CreateSchedule':
            entered.set()
            assert release.wait(3)
        return bus(method, *args, **kwargs)
    view, _ = make_dialog(call)
    wait_for(lambda: not view.busy)
    view.entry.set_text('hourly')
    view.add_button_control.clicked()
    try:
        wait_for(entered.is_set)
        assert view.busy
        assert not view.close_button.get_sensitive()
        view._request('CreateSchedule')
        view.response(Gtk.ResponseType.CLOSE)
        assert not view._closed
    finally:
        release.set()
    wait_for(lambda: not view.busy)
    assert sum(name == 'CreateSchedule' for name, _, _ in bus.calls) == 1


def test_destroy_ignores_late_result(make_dialog):
    release = threading.Event()
    done = threading.Event()
    def call(*args, **kwargs):
        try:
            assert release.wait(3)
            return GLib.Variant('(s)', ('{"id":7,"schedule":null}',))
        finally:
            done.set()
    view, changed = make_dialog(call)
    view.destroy()
    release.set()
    wait_for(done.is_set)
    wait_for(lambda: view._closed)
    assert view._finish(None, [], '', 'PauseSchedule') == GLib.SOURCE_REMOVE
    changed.assert_not_called()


def test_task_card_routes_schedule_button(make_dialog):
    callback = Mock()
    board = TaskBoard(Mock(), Mock(), Mock(), Mock(), callback)
    try:
        for schedule, label in [(None, 'Schedule'), ({'schedule_text': 'hourly', 'enabled': False}, 'Manage schedule')]:
            card = board._card(dict(id=7, idea='Check disk', status='done', schedule=schedule))
            card.show_all()
            actions = card.get_children()[-1]
            button = next(w for w in actions.get_children() if w.get_label() == label)
            button.clicked()
            callback.assert_called_with(7)
            card.destroy()
    finally:
        board.destroy()
