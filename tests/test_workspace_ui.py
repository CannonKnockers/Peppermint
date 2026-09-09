"""Window integration: menu navigation, draft retention and monitoring visibility."""

import time

import gi
import pytest

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from peppermint.common import dbus_api
from peppermint.ui.window import PeppermintWindow


class Reader:
    def __init__(self):
        self.requests = []
        self.closed = False

    def request(self, key, method, params, callback):
        self.requests.append((key, method, params.unpack(), callback))

    def close(self):
        self.closed = True


class Diagnostics(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.active = False
        self.context = None

    def set_active(self, active):
        self.active = active

    def set_task_context(self, task):
        self.context = task


@pytest.fixture
def window(monkeypatch):
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')
    class Bus:
        def signal_subscribe(self, *_):
            return 0
    monkeypatch.setattr(dbus_api, 'session_bus', lambda: Bus())
    view = PeppermintWindow(None, reader=Reader(), diagnostics=Diagnostics())
    yield view
    view.destroy()


def task(task_id=1, **extra):
    return dict(id=task_id, idea=f'Task {task_id}', status='done', **extra)


def report(**extra):
    return dict(tasks=[], counts={}, total=0, matched=0, offset=0, limit=40, has_more=False, **extra)


def wait_until(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)
    assert predicate()


def test_menu_uses_an_accessible_image_and_preserves_native_window_controls(window):
    window.show_all()
    assert isinstance(window.menu_button, Gtk.ToggleButton)
    assert not isinstance(window.menu_button, Gtk.MenuButton)
    assert isinstance(window.menu_button.get_child(), Gtk.Image)
    assert window.menu_button.get_accessible().get_name() == 'Peppermint menu'
    assert window.menu_revealer.get_child() is window.main_menu
    assert window.menu_revealer.get_transition_type() == Gtk.RevealerTransitionType.SLIDE_RIGHT
    assert not window.menu_layer.get_visible()
    assert window.header.get_show_close_button()
    assert window.header.get_decoration_layout() == ':minimize,maximize,close'
    for name, title in (
        ('tasks', 'Tasks'), ('conversations', 'Conversations'),
        ('diagnostics', 'Diagnostics'), ('manual', 'User manual'),
    ):
        window.main_menu.navigation[name].clicked()
        assert window.pages.get_visible_child_name() == name
        assert window.page_title.get_text() == title
        assert window.main_menu.navigation[name].get_accessible().get_name() == title
    assert window.get_focus() is window.manual.search


def test_refresh_is_deferred_and_destroy_invalidates_reads(window):
    assert window.rows == {}
    request = window._reader.requests[-1]
    assert request[1] == 'ListTasks'
    callback = request[-1]
    window.destroy()
    assert window._reader.closed
    callback([task()], None)
    assert window.rows == {}


def test_old_conversation_and_drafts_survive_newest_page_and_menu_navigation(window):
    window._accept_tasks([task()], None)
    row = window.rows[1]
    window.open_task(1)
    window._accept_detail(task(), None)
    window.conversation_entry.set_text('My unfinished follow-up')
    window.entry.set_text('My next task')
    window.main_menu.navigation['diagnostics'].clicked()
    window._accept_tasks([task(i) for i in range(40, 80)], None)
    window.main_menu.navigation['manual'].clicked()
    window.main_menu.navigation['tasks'].clicked()
    window.main_menu.navigation['conversations'].clicked()
    assert window.rows[1] is row
    assert window.conversation_entry.get_text() == 'My unfinished follow-up'
    assert window.entry.get_text() == 'My next task'
    assert len(window.rows) == 41


def test_open_older_task_loads_detail_and_switches_view(window):
    window.open_task(200)
    assert window.pages.get_visible_child_name() == 'conversations'
    assert window._reader.requests[-1][2] == (200,)
    window._reader.requests[-1][-1](task(200), None)
    assert window.rows[200].expanded


def test_manual_and_hide_menu_keep_monitoring_enabled_without_losing_history(window):
    window._accept_tasks([task()], None)
    row = window.rows[1]
    window.entry.set_text('Keep this unfinished idea')
    window.show_all()
    window.main_menu.navigation['diagnostics'].clicked()
    assert window.diagnostics.active
    window.main_menu.navigation['manual'].clicked()
    assert window.diagnostics.active
    window.main_menu.navigation['diagnostics'].clicked()
    assert window.diagnostics.active
    window.main_menu.hide_button.clicked()
    assert not window.get_visible()
    assert window.diagnostics.active
    assert not window._reader.closed
    window.show_all()
    assert window.diagnostics.active
    assert window.rows[1] is row
    assert window.entry.get_text() == 'Keep this unfinished idea'
    assert window._on_close()
    assert window.diagnostics.active


def test_menu_new_conversation_focuses_existing_draft_without_submitting(window, monkeypatch):
    submitted = []
    monkeypatch.setattr(dbus_api, 'call_daemon', lambda *args, **kwargs: submitted.append(args))
    window.show_all()
    window.entry.set_text('An unfinished idea to review first')
    window.main_menu.navigation['manual'].clicked()
    window.menu_button.set_active(True)
    window.main_menu.new_button.clicked()
    assert window.menu_button.get_active()
    assert window.pages.get_visible_child_name() == 'conversations'
    assert window.get_focus() is window.entry
    assert window.entry.get_text() == 'An unfinished idea to review first'
    assert submitted == []


def test_menu_refresh_reads_current_overview_and_open_conversations(window):
    window._accept_tasks([task()], None)
    window.open_task(1)
    window.task_board.filter.set_active_id('waiting')
    window.task_board.search.set_text('printer')
    window._reader.requests.clear()
    window.main_menu.refresh_button.clicked()
    wait_until(lambda: any(request[1] == 'TaskOverview' for request in window._reader.requests))
    requests = [(method, params) for _, method, params, _ in window._reader.requests]
    assert ('ListTasks', (40,)) in requests
    assert ('GetTask', (1,)) in requests
    assert ('TaskOverview', ('waiting', 'printer', 0, 40)) in requests
    assert all(method in {'ListTasks', 'GetTask', 'TaskOverview'} for method, _ in requests)


def test_escape_dismisses_open_menu_before_hiding_window(window):
    window.show_all()
    window.menu_button.grab_focus()
    window.menu_button.set_active(True)
    wait_until(lambda: window.main_menu.get_mapped())
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.window = window.get_window()
    event.send_event = True
    event.time = Gdk.CURRENT_TIME
    event.keyval = Gdk.KEY_Escape
    event.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
    Gtk.main_do_event(event)
    wait_until(lambda: not window.main_menu.get_mapped())
    assert window.get_visible()
    assert not window.menu_button.get_active()
    assert window.get_focus() is window.menu_button
    Gtk.main_do_event(event)
    assert not window.get_visible()


def test_sidebar_resizes_page_and_keeps_both_usable(window):
    window.show_all()
    wait_until(lambda: window.get_mapped())
    width = window.workspace.get_allocated_width()
    window.menu_button.set_active(True)
    wait_until(lambda: window.menu_revealer.get_child_revealed())
    assert window.workspace.get_sensitive()
    assert window.menu_revealer.translate_coordinates(window.overlay, 0, 0) == (0, 0)
    assert window.workspace.get_allocated_width() < width
    window.entry.set_text('Can write with sidebar open')
    window.main_menu.navigation['manual'].clicked()
    assert window.menu_button.get_active()
    assert window.manual.search.is_sensitive()
    window.main_menu.navigation['conversations'].clicked()
    assert window.entry.get_text() == 'Can write with sidebar open'
    window.main_menu.close_button.clicked()
    wait_until(lambda: not window.menu_layer.get_visible())
    window.show_all()
    assert not window.menu_layer.get_visible()


def test_sidebar_close_button_and_disabled_animations(window):
    settings = Gtk.Settings.get_default()
    original = settings.get_property('gtk-enable-animations')
    try:
        settings.set_property('gtk-enable-animations', False)
        window.show_all()
        window.menu_button.set_active(True)
        wait_until(lambda: window.menu_revealer.get_child_revealed())
        window.main_menu.close_button.clicked()
        wait_until(lambda: not window.menu_layer.get_visible())
        assert window.get_focus() is window.menu_button
    finally:
        settings.set_property('gtk-enable-animations', original)


def test_diagnostics_context_ignores_previous_task_response(window):
    window.open_diagnostics(1)
    old_callback = window._reader.requests[-1][-1]
    window.open_diagnostics(2)
    old_callback(task(1), None)
    assert window.diagnostics.context is None
    window._reader.requests[-1][-1](task(2), None)
    assert window.diagnostics.context['id'] == 2


def test_filter_change_rejects_response_during_debounce(window):
    window._query_overview('all', '', 0)
    callback = window._reader.requests[-1][-1]
    window.task_board.filter.set_active_id('waiting')
    callback(report(), None)
    assert window.task_board.report == {}
    assert window.task_board.offset == 0


def test_page_change_rejects_response_during_debounce(window):
    window._query_overview('all', '', 0)
    callback = window._reader.requests[-1][-1]
    window.task_board._page(1)
    callback(report(), None)
    assert window.task_board.offset == 40
    assert window.task_board.report == {}


def test_task_counts_use_global_counts_not_page_length(window):
    window.task_board.update_report(dict(tasks=[task()], counts={'done': 200, 'running': 2,
        'queued': 1, 'awaiting-input': 3, 'awaiting-confirmation': 4}, total=210, matched=200,
        offset=0, limit=40, has_more=True))
    assert window.task_board.counters['active'].get_text() == '3'
    assert window.task_board.counters['waiting'].get_text() == '7'
    assert window.task_board.counters['total'].get_text() == '210'
    assert window.task_board.page_label.get_text() == '1–1 of 200'
    assert window.task_board.next.get_sensitive()


def test_chat_switch_keeps_one_transcript_and_each_draft(window):
    window._accept_tasks([task(1), task(2)], None)
    window.open_task(1)
    window._accept_detail(task(1, messages=[dict(role='user', content='First')]), None)
    window.conversation_entry.set_text('Draft one')
    window.list.select_row(window._sidebar_rows[2])
    window._accept_detail(task(2, messages=[dict(role='user', content='Second')]), None)
    window.conversation_entry.set_text('Draft two')
    window._accept_detail(task(1, messages=[dict(role='user', content='Delayed first')]), None)
    assert window._active_task_id == 2
    assert window.conversation_panel.get_children() == [window.rows[2]]
    assert window.conversation_entry.get_text() == 'Draft two'
    window.open_task(1)
    assert window.conversation_entry.get_text() == 'Draft one'
    assert window.conversation_panel.get_children() == [window.rows[1]]
    window.new_task()
    assert window.entry.get_visible()
    assert not window.conversation_entry.get_visible()
    assert window.rows[1]._chat_draft == 'Draft one'


def test_active_approval_remains_in_transcript_with_followup_disabled(window):
    pending = dict(id=17, description='Inspect the selected file')
    data = dict(id=1, idea='Inspect', status='awaiting-confirmation', pending=pending)
    window.open_task(1)
    window._accept_detail(data, None)
    assert not window.conversation_send.get_sensitive()
    assert window.rows[1].stop_button.get_visible()
    def descendants(widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from descendants(child)
    buttons = [w for w in descendants(window.conversation_panel) if isinstance(w, Gtk.Button)]
    assert {'Allow once', 'Deny', 'Stop'} <= {w.get_label() for w in buttons}


def test_followup_failure_restores_draft(window, monkeypatch):
    window.open_task(1)
    window._accept_detail(task(), None)
    window.conversation_entry.set_text('Keep this')
    def fail(*_):
        raise RuntimeError('offline')
    monkeypatch.setattr(window, 'chat', fail)
    with pytest.raises(RuntimeError):
        window._on_active_chat()
    assert window.rows[1]._chat_draft == 'Keep this'


def test_history_is_only_in_sidebar_and_selection_keeps_it_open(window):
    window._accept_tasks([task(1), task(2)], None)
    window.show_all()
    assert window.list.is_ancestor(window.main_menu)
    assert not window.list.get_mapped()
    window.menu_button.set_active(True)
    wait_until(lambda: window.list.get_mapped())
    window.list.select_row(window._sidebar_rows[2])
    assert window.menu_button.get_active()
    assert window.workspace.get_sensitive()
    assert window._active_task_id == 2
    assert window.list.get_mapped()
    assert window.pages.get_visible_child_name() == 'conversations'


def test_menu_recovery_stays_in_main_window_and_returns_to_chat(window, monkeypatch):
    from peppermint.recovery import app as recovery
    monkeypatch.setattr(recovery.processes, 'list_processes', lambda: [])
    monkeypatch.setattr(recovery.RecoveryPanel, '_load_session_controls', lambda self: None)
    window.entry.set_text('Preserve this draft')
    before = set(Gtk.Window.list_toplevels())
    window.menu_button.set_active(True)
    window.main_menu.recovery_button.clicked()
    assert set(Gtk.Window.list_toplevels()) == before
    panel = window._recovery_panel
    assert panel.get_toplevel() is window
    assert window.pages.get_visible_child_name() == 'recovery'
    panel.return_button.clicked()
    assert window.pages.get_visible_child_name() == 'conversations'
    assert window.entry.get_text() == 'Preserve this draft'
    window.open_recovery()
    assert window._recovery_panel is panel
    window.destroy()
    assert panel._closed.is_set()


def test_chat_composer_masks_password_request_and_resets_on_chat_switch(window):
    window.open_task(1)
    window._accept_detail(task(1, messages=[dict(role='assistant', content='Please enter your password.')]), None)
    assert not window.conversation_entry.get_visibility()
    window.conversation_entry.set_text('example-secret')
    window.open_task(2)
    window._accept_detail(task(2, messages=[dict(role='assistant', content='What application?')]), None)
    assert window.conversation_entry.get_visibility()
    window.open_task(1)
    assert not window.conversation_entry.get_visibility()
    assert window.conversation_entry.get_text() == 'example-secret'


def test_started_diagnostics_collects_off_page_and_hidden_until_paused(window):
    from tests.test_diagnostics_view import view, sample, flush
    diagnostics = view()
    host = PeppermintWindow(None, reader=Reader(), diagnostics=diagnostics)
    try:
        host.show_all()
        assert not diagnostics.monitoring
        host.navigate('diagnostics')
        diagnostics.start_button.clicked()
        monitor = diagnostics._monitor
        assert diagnostics.monitoring
        host.navigate('conversations')
        monitor.callback(sample(0))
        flush()
        host.hide()
        monitor.callback(sample(2))
        flush()
        assert diagnostics.monitoring
        assert len(diagnostics.history) == 2
        assert monitor.starts == 1 and monitor.stops == 0
        host.show_all()
        host.navigate('diagnostics')
        assert diagnostics.history[-1]['monotonic'] == 1002
        diagnostics.start_button.clicked()
        assert not diagnostics.monitoring
        monitor.callback(sample(4))
        flush()
        assert len(diagnostics.history) == 2
    finally:
        host.destroy()
    assert not monitor.running


def test_process_tracking_runs_without_graphs_until_report_selected(window, tmp_path):
    from tests.test_diagnostics_view import view, sample, process, flush
    from peppermint.diagnostics.tracking import ProcessTracking
    diagnostics = view()
    host = PeppermintWindow(None, reader=Reader(), diagnostics=diagnostics)
    host.tracking = ProcessTracking(tmp_path)
    try:
        host.show_all()
        host.navigate('diagnostics')
        diagnostics.ingest_sample(sample(processes=[process()]))
        diagnostics._selected_identity = (12, 900)
        diagnostics._selected_name = 'fixture-player'
        diagnostics._refresh_selected_details()
        diagnostics.track_button.clicked()
        assert diagnostics.monitoring
        assert host._reports_view is None
        rid = next(iter(host.tracking.reports))
        host.navigate('conversations')
        diagnostics._monitor.callback(sample(2, processes=[process()]))
        flush()
        assert len(host.tracking.reports[rid]['points']) == 1
        assert host._reports_view is None
        host._open_tracking_report(rid)
        assert len(host._reports_view.charts) == 4
        assert host.pages.get_visible_child_name() == 'tracking'
        host._reports_view.stop.clicked()
        assert not host.tracking.reports[rid]['active']
        assert list(tmp_path.glob('*.json'))
        assert not host.main_menu.history_expander.get_expanded()
    finally:
        host.destroy()


def test_schedule_controls_reuse_dialog_and_refresh(window, monkeypatch):
    from unittest.mock import Mock
    from peppermint.ui import schedule_dialog
    created = []

    class Dialog(Gtk.Dialog):
        def __init__(self, parent, task_id, on_changed):
            super().__init__(transient_for=parent)
            self.task_id = task_id
            self.on_changed = on_changed
            created.append(self)

    monkeypatch.setattr(schedule_dialog, 'ScheduleDialog', Dialog)
    window.refresh = Mock()
    assert window.task_board._on_schedule == window.open_schedule
    window.open_schedule(7)
    window.open_schedule(8)
    assert len(created) == 1
    assert created[0].task_id == 7
    created[0].on_changed()
    window.refresh.assert_called_once()
    created[0].destroy()
    assert window._schedule_dialog is None
    window.open_schedule(8)
    assert created[1].task_id == 8
    window.destroy()
    assert window._schedule_dialog is None


def test_plugins_sidebar_opens_in_same_window_and_refreshes(window, monkeypatch):
    from unittest.mock import Mock
    reload = Mock()
    monkeypatch.setattr(window.plugins_view, 'reload', reload)
    before = set(Gtk.Window.list_toplevels())
    window.main_menu.navigation['plugins'].clicked()
    assert window.pages.get_visible_child_name() == 'plugins'
    assert window.page_title.get_text() == 'Plugins'
    assert set(Gtk.Window.list_toplevels()) == before
    reload.assert_called_once()
    window.refresh()
    assert reload.call_count == 2
