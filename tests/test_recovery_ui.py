"""Recovery GTK behavior with fixture snapshots and callbacks; no real signals."""

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from peppermint.recovery import app


def flush():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def finish(window):
    window._worker.join(timeout=2)
    assert not window._worker.is_alive()
    flush()
    assert not window._busy or window._closed.is_set()


def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        flush()
        time.sleep(0.005)
    assert predicate()


def process(pid=987654, start=111, uid=None, name='fixture-editor'):
    return dict(pid=pid, start_ticks=start, uid=os.getuid() if uid is None else uid,
                name=name, state='S', rss_bytes=2 * 1024 ** 2)


@pytest.fixture
def windows(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')
    monkeypatch.setattr(app.admin, 'helper_ready', lambda: True)
    # Every route defaults to a failure so a missing fixture cannot signal a PID.
    def forbidden(*args, **kwargs):
        raise AssertionError('A real recovery callback must never run in UI tests')
    monkeypatch.setattr(app.processes, 'list_processes', forbidden)
    monkeypatch.setattr(app.processes, 'act', forbidden)
    monkeypatch.setattr(app.admin, 'act_as_admin', forbidden)
    monkeypatch.setattr(app.admin, 'run_helper', forbidden)
    instances = []
    def create(**kwargs):
        kwargs.setdefault('list_processes', lambda: [process()])
        kwargs.setdefault('session_capabilities', lambda: {})
        kwargs.setdefault('session_request', forbidden)
        window = app.RecoveryWindow(**kwargs)
        instances.append(window)
        return window
    yield create
    for window in instances:
        window.destroy()
        if window._worker:
            window._worker.join(timeout=2)
    flush()


@pytest.fixture
def confirmation(monkeypatch):
    dialogs = []
    class Dialog:
        answer = Gtk.ResponseType.CANCEL
        def __init__(self, **kwargs):
            self.options = kwargs
            self.destroyed = False
            dialogs.append(self)
        def format_secondary_text(self, value):
            self.explanation = value
        def add_button(self, label, response):
            self.action = (label, response)
        def set_default_response(self, response):
            self.default_response = response
        def run(self):
            return self.answer
        def destroy(self):
            self.destroyed = True
    monkeypatch.setattr(app.Gtk, 'MessageDialog', Dialog)
    return SimpleNamespace(dialogs=dialogs, dialog=Dialog)


def select(window, index=0):
    window.tree.get_selection().select_path(str(index))
    assert window._selected is not None


def test_open_and_refresh_only_read_and_keep_gtk_usable_during_pending_read(windows):
    started, release = threading.Event(), threading.Event()
    read_threads, actions = [], []
    def snapshot():
        read_threads.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=2)
        return [process()]
    window = windows(list_processes=snapshot, act=lambda *args: actions.append(args),
                     admin_act=lambda *args: actions.append(args))
    try:
        assert started.wait(timeout=1)
        assert window._busy and not window.refresh_button.get_sensitive()
        assert window.back.get_sensitive()
        marker = []
        GLib.idle_add(lambda: marker.append('responsive') and False)
        flush()
        assert marker == ['responsive']
        assert len(window.store) == 0
        assert read_threads == [window._worker.ident]
        assert read_threads[0] != threading.get_ident()
    finally:
        release.set()
    finish(window)
    select(window)
    assert window.stop_button.get_sensitive()
    assert not window.force_button.get_sensitive()
    window.refresh_button.clicked()
    finish(window)
    assert len(read_threads) == 2
    assert not actions


def test_refresh_retains_exact_identity_and_clears_selection_and_force_on_pid_reuse(windows):
    rows = [process(), process(pid=987655, start=222, name='fixture-player')]
    window = windows(list_processes=lambda: list(rows))
    finish(window)
    select(window)
    identity = window._selected
    window._force_allowed.add(identity)
    rows.reverse()
    window.refresh()
    finish(window)
    assert window._selected == identity
    assert window.force_button.get_sensitive()
    rows[1] = process(start=333, name='replacement')
    window.refresh()
    finish(window)
    assert window._selected is None
    assert identity not in window._force_allowed
    assert not window.stop_button.get_sensitive()
    assert not window.force_button.get_sensitive()
    select(window, 1)
    assert window._selected == app.RecoveryWindow._identity(rows[1])
    assert not window.force_button.get_sensitive()


def test_owner_change_also_invalidates_selection(windows):
    rows = [process()]
    window = windows(list_processes=lambda: list(rows))
    finish(window)
    select(window)
    rows[0] = process(uid=os.getuid() + 1)
    window.refresh()
    finish(window)
    assert window._selected is None
    assert not window.stop_button.get_sensitive()


def test_cancelled_confirmation_never_invokes_either_action_route(windows, confirmation):
    actions = []
    window = windows(act=lambda *args: actions.append(args), admin_act=lambda *args: actions.append(args))
    finish(window)
    select(window)
    window.stop_button.clicked()
    flush()
    assert len(confirmation.dialogs) == 1
    dialog = confirmation.dialogs[0]
    assert dialog.default_response == Gtk.ResponseType.CANCEL
    assert dialog.destroyed and dialog.options['modal']
    assert 'fixture-editor' in dialog.options['text']
    assert str(process()['pid']) in dialog.options['text']
    assert 'Unsaved work' in dialog.explanation
    assert not actions and not window._busy


@pytest.mark.parametrize('foreign', [False, True])
def test_confirmed_action_routes_exact_captured_identity(windows, confirmation, foreign):
    row = process(uid=os.getuid() + 1 if foreign else os.getuid())
    local_calls, admin_calls = [], []
    window = windows(list_processes=lambda: [row],
                     act=lambda *args: local_calls.append(args) or {'status': 'timeout', 'message': 'Still running'},
                     admin_act=lambda *args: admin_calls.append(args) or {'status': 'timeout', 'message': 'Still running'})
    finish(window)
    select(window)
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window.stop_button.clicked()
    finish(window)
    if foreign:
        assert not local_calls
        target, action, cancelled = admin_calls[0]
        assert target == row and target is not row
        assert action == 'terminate'
        assert callable(cancelled) and not cancelled()
        window.destroy()
        assert cancelled()
    else:
        assert local_calls == [(row['pid'], row['start_ticks'], row['uid'], 'terminate')]
        assert not admin_calls


@pytest.mark.parametrize('status', ['denied', 'error', 'protected', 'mismatch', 'cancelled', 'exited'])
def test_force_remains_unavailable_without_a_term_timeout(windows, confirmation, status):
    calls = []
    window = windows(act=lambda *args: calls.append(args) or {'status': status, 'message': status})
    finish(window)
    select(window)
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window._confirm_action('kill')
    assert not confirmation.dialogs and not calls
    window.stop_button.clicked()
    finish(window)
    assert len(calls) == 1 and calls[0][-1] == 'terminate'
    assert not window.force_button.get_sensitive()
    window._confirm_action('kill')
    assert len(calls) == 1 and len(confirmation.dialogs) == 1


def test_term_timeout_requires_separate_confirmation_before_force(windows, confirmation):
    calls = []
    def action(*args):
        calls.append(args)
        return {'status': 'timeout' if args[-1] == 'terminate' else 'exited', 'message': 'Fixture result'}
    window = windows(act=action)
    finish(window)
    select(window)
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window.stop_button.clicked()
    finish(window)
    assert window.force_button.get_sensitive()
    assert [call[-1] for call in calls] == ['terminate']
    confirmation.dialog.answer = Gtk.ResponseType.CANCEL
    window.force_button.clicked()
    assert [call[-1] for call in calls] == ['terminate']
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window.force_button.clicked()
    finish(window)
    assert [call[-1] for call in calls] == ['terminate', 'kill']
    assert not window.stop_button.get_sensitive()
    assert not window.force_button.get_sensitive()
    assert len(window.store) == 0 and window._selected is None


def test_protected_process_is_visible_but_cannot_open_a_stop_confirmation(windows, confirmation):
    row = process()
    row['protected'] = True
    row['protected_reason'] = 'This process is part of the desktop session.'
    calls = []
    window = windows(list_processes=lambda: [row], act=lambda *args: calls.append(args))
    finish(window)
    select(window)
    assert row['protected_reason'] in window.detail.get_text()
    assert not window.stop_button.get_sensitive()
    assert not window.force_button.get_sensitive()
    window._confirm_action('terminate')
    window._confirm_action('kill')
    assert not calls and not confirmation.dialogs


def test_close_during_admin_worker_propagates_cancellation_without_waiting(windows, confirmation):
    started, release = threading.Event(), threading.Event()
    callbacks = []
    def action(target, action, cancelled):
        callbacks.append(cancelled)
        started.set()
        assert release.wait(timeout=2)
        return {'status': 'cancelled', 'message': 'Fixture cancelled'}
    window = windows(list_processes=lambda: [process(uid=os.getuid() + 1)], admin_act=action)
    finish(window)
    select(window)
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window.stop_button.clicked()
    try:
        assert started.wait(timeout=1)
        assert not callbacks[0]()
        window.back.clicked()
        assert callbacks[0]()
        assert window._worker.is_alive()
    finally:
        release.set()
    window._worker.join(timeout=2)
    flush()
    assert not window._worker.is_alive()


@pytest.mark.parametrize('close', ['button', 'escape'])
def test_close_returns_control_while_worker_pending_and_discards_late_result(windows, close):
    started, release = threading.Event(), threading.Event()
    def snapshot():
        started.set()
        assert release.wait(timeout=2)
        return [process()]
    window = windows(list_processes=snapshot)
    try:
        assert started.wait(timeout=1)
        assert window._worker.is_alive()
        if close == 'button':
            window.back.clicked()
        else:
            assert window._key(window, SimpleNamespace(keyval=Gdk.KEY_Escape))
        assert window._closed.is_set()
        assert window._worker.is_alive()
        marker = []
        GLib.idle_add(lambda: marker.append(True) and False)
        flush()
        assert marker == [True]
    finally:
        release.set()
    window._worker.join(timeout=2)
    flush()
    assert not window._rows


def test_admin_access_check_authenticates_without_signaling_any_process(windows, monkeypatch):
    checks, actions = [], []
    monkeypatch.setattr(app.admin, 'run_helper', lambda *args: checks.append(args) or {'status': 'ok', 'euid': 0})
    window = windows(act=lambda *args: actions.append(args), admin_act=lambda *args: actions.append(args))
    finish(window)
    window.admin_button.clicked()
    finish(window)
    assert len(checks) == 1 and checks[0][0] == ['--check']
    assert callable(checks[0][1]) and not checks[0][1]()
    assert 'verified' in window.admin_status.get_text()
    assert not actions


def test_read_failure_is_visible_and_refresh_can_retry(windows):
    reads = []
    def snapshot():
        reads.append(True)
        if len(reads) == 1:
            raise OSError('Fixture read denied')
        return [process()]
    window = windows(list_processes=snapshot)
    finish(window)
    assert 'Fixture read denied' in window.status.get_text()
    assert window.refresh_button.get_sensitive()
    window.refresh_button.clicked()
    finish(window)
    assert len(window._rows) == 1


def test_session_capabilities_load_in_background_and_hide_unsupported_sleep(windows):
    calls, requests = [], []
    capabilities = {'lock': True, 'logout': True, 'restart': True, 'shutdown': True,
                    'switch_user': False, 'suspend': True, 'hibernate': False}
    def read_capabilities():
        calls.append(threading.get_ident())
        return capabilities
    window = windows(session_capabilities=read_capabilities, session_request=lambda action: requests.append(action))
    finish(window)
    wait_until(lambda: window._capabilities == capabilities)
    window.show_all()
    flush()
    assert calls and calls[0] != threading.get_ident()
    assert not requests
    for action, available in capabilities.items():
        assert window.session_buttons[action].get_sensitive() == available
    assert window.session_buttons['suspend'].get_parent().get_visible()
    assert not window.session_buttons['hibernate'].get_parent().get_visible()
    assert window.session_buttons['switch_user'].get_parent().get_visible()


def test_pending_session_capability_read_cannot_block_process_recovery_or_closing(windows):
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    def read_capabilities():
        started.set()
        assert release.wait(timeout=2)
        returned.set()
        return {'lock': True}
    window = windows(session_capabilities=read_capabilities)
    try:
        assert started.wait(timeout=1)
        finish(window)
        select(window)
        assert window.stop_button.get_sensitive()
        assert not window.session_buttons['lock'].get_sensitive()
        window.back.clicked()
        assert window._closed.is_set()
    finally:
        release.set()
    assert returned.wait(timeout=1)
    flush()
    assert not window._capabilities


def test_all_false_session_capabilities_report_unavailable_without_blocking_recovery(windows):
    capabilities = dict.fromkeys(('lock', 'switch_user', 'logout', 'suspend', 'hibernate', 'restart', 'shutdown'), False)
    window = windows(session_capabilities=lambda: capabilities)
    finish(window)
    wait_until(lambda: window._capabilities == capabilities)
    assert 'unavailable' in window.session_status.get_text().lower()
    assert all(not button.get_sensitive() for button in window.session_buttons.values())
    select(window)
    assert window.stop_button.get_sensitive()


def test_small_window_scrolls_to_session_controls_and_back_to_desktop_exit(windows):
    capabilities = dict.fromkeys(('lock', 'switch_user', 'logout', 'suspend', 'hibernate', 'restart', 'shutdown'), True)
    window = windows(session_capabilities=lambda: capabilities)
    finish(window)
    wait_until(lambda: window._capabilities == capabilities)
    window.set_default_size(660, 560)
    window.show_all()
    window.resize(660, 560)
    wait_until(lambda: tuple(window.get_size()) == (660, 560))
    page = window.get_child()
    assert isinstance(page, Gtk.ScrolledWindow)
    adjustment = page.get_vadjustment()
    wait_until(lambda: adjustment.get_page_size() > 0 and adjustment.get_upper() > adjustment.get_page_size())
    assert window.tree.get_allocated_height() >= 100
    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
    flush()
    button = window.session_buttons['shutdown']
    _x, y = button.translate_coordinates(page, 0, 0)
    assert 0 <= y and y + button.get_allocated_height() <= page.get_allocated_height()
    assert button.get_sensitive()
    adjustment.set_value(adjustment.get_lower())
    flush()
    _x, y = window.back.translate_coordinates(page, 0, 0)
    assert 0 <= y and y + window.back.get_allocated_height() <= page.get_allocated_height()
    assert window.back.get_sensitive()


@pytest.mark.parametrize('action', ['lock', 'switch_user', 'suspend', 'hibernate'])
def test_cancelled_session_confirmation_never_calls_desktop(windows, confirmation, action):
    requests = []
    window = windows(session_capabilities=lambda: {action: True},
                     session_request=lambda value: requests.append(value))
    finish(window)
    wait_until(lambda: window._capabilities.get(action))
    window.show_all()
    window.session_buttons[action].clicked()
    assert not requests
    assert window.get_visible()
    assert len(confirmation.dialogs) == 1
    assert confirmation.dialogs[0].default_response == Gtk.ResponseType.CANCEL


@pytest.mark.parametrize('action', ['lock', 'switch_user', 'logout', 'suspend', 'hibernate', 'restart', 'shutdown'])
@pytest.mark.parametrize('outcome', ['requested', 'unknown'])
def test_session_request_or_unknown_timeout_yields_screen_without_retry(windows, confirmation, action, outcome):
    requests = []
    window = windows(session_capabilities=lambda: {action: True},
                     session_request=lambda value: requests.append(value) or {'status': outcome, 'message': 'Fixture desktop request'})
    finish(window)
    wait_until(lambda: window._capabilities.get(action))
    window.show_all()
    confirmation.dialog.answer = Gtk.ResponseType.OK
    window.session_buttons[action].clicked()
    finish(window)
    assert requests == [action]
    assert not window.get_visible()
    assert not window._closed.is_set()
    if action in ('logout', 'restart', 'shutdown'):
        # Mint owns its native confirmation/inhibitor dialogs for these actions.
        assert not confirmation.dialogs
    else:
        assert len(confirmation.dialogs) == 1


@pytest.mark.parametrize('raises', [False, True])
def test_failed_session_request_restores_recovery_and_reports_error(windows, raises):
    def request(action):
        assert action == 'logout'
        if raises:
            raise OSError('Fixture desktop unavailable')
        return {'status': 'error', 'message': 'Fixture desktop unavailable'}
    window = windows(session_capabilities=lambda: {'logout': True}, session_request=request)
    finish(window)
    wait_until(lambda: window._capabilities.get('logout'))
    window.show_all()
    window.session_buttons['logout'].clicked()
    finish(window)
    assert window.get_visible()
    assert 'Fixture desktop unavailable' in window.session_status.get_text()
    assert window.back.get_sensitive()
    assert window.session_buttons['logout'].get_sensitive()


def test_unsupported_session_action_never_opens_confirmation_or_calls_desktop(windows, confirmation):
    requests = []
    window = windows(session_capabilities=lambda: {'lock': True, 'hibernate': False},
                     session_request=lambda value: requests.append(value))
    finish(window)
    wait_until(lambda: window._capabilities.get('lock'))
    window._session_action('hibernate')
    window._session_action('unknown')
    assert not requests and not confirmation.dialogs


def test_recovery_import_does_not_load_main_window_daemon_or_model():
    code = '''
import importlib.abc
import sys
class BlockMainApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname in ('ollama', 'peppermint.cli', 'peppermint.common.dbus_api')
                or fullname.startswith(('peppermint.daemon', 'peppermint.ui', 'peppermint.diagnostics'))):
            raise AssertionError('Recovery imported normal application dependency: ' + fullname)
sys.meta_path.insert(0, BlockMainApplication())
import peppermint.recovery.app
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
