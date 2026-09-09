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
    row.expanded = True
    row.update(task())
    row._chat_entry.set_text('My unfinished follow-up')
    window.entry.set_text('My next task')
    window.main_menu.navigation['diagnostics'].clicked()
    window._accept_tasks([task(i) for i in range(40, 80)], None)
    window.main_menu.navigation['manual'].clicked()
    window.main_menu.navigation['tasks'].clicked()
    window.main_menu.navigation['conversations'].clicked()
    assert window.rows[1] is row
    assert row._chat_entry.get_text() == 'My unfinished follow-up'
    assert window.entry.get_text() == 'My next task'
    assert len(window.rows) == 41


def test_open_older_task_loads_detail_and_switches_view(window):
    window.open_task(200)
    assert window.pages.get_visible_child_name() == 'conversations'
    assert window._reader.requests[-1][2] == (200,)
    window._reader.requests[-1][-1](task(200), None)
    assert window.rows[200].expanded


def test_manual_and_hide_menu_deactivate_monitoring_without_losing_history(window):
    window._accept_tasks([task()], None)
    row = window.rows[1]
    window.entry.set_text('Keep this unfinished idea')
    window.show_all()
    window.main_menu.navigation['diagnostics'].clicked()
    assert window.diagnostics.active
    window.main_menu.navigation['manual'].clicked()
    assert not window.diagnostics.active
    window.main_menu.navigation['diagnostics'].clicked()
    assert window.diagnostics.active
    window.main_menu.hide_button.clicked()
    assert not window.get_visible()
    assert not window.diagnostics.active
    assert not window._reader.closed
    window.show_all()
    assert window.diagnostics.active
    assert window.rows[1] is row
    assert window.entry.get_text() == 'Keep this unfinished idea'
    assert window._on_close()
    assert not window.diagnostics.active


def test_menu_new_conversation_focuses_existing_draft_without_submitting(window, monkeypatch):
    submitted = []
    monkeypatch.setattr(dbus_api, 'call_daemon', lambda *args, **kwargs: submitted.append(args))
    window.show_all()
    window.entry.set_text('An unfinished idea to review first')
    window.main_menu.navigation['manual'].clicked()
    window.menu_button.set_active(True)
    window.main_menu.new_button.clicked()
    wait_until(lambda: not window.main_menu.get_mapped())
    assert window.pages.get_visible_child_name() == 'conversations'
    assert window.get_focus() is window.entry
    assert window.entry.get_text() == 'An unfinished idea to review first'
    assert submitted == []


def test_menu_refresh_reads_current_overview_and_open_conversations(window):
    window._accept_tasks([task()], None)
    window.rows[1].expanded = True
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


def test_sidebar_fills_left_edge_without_resizing_content_and_closes_outside(window):
    window.show_all()
    window.resize(660, 560)
    wait_until(lambda: window.get_mapped() and window.get_size().width == 660)
    width = window.get_size().width
    window.menu_button.set_active(True)
    wait_until(lambda: window.menu_revealer.get_child_revealed())
    assert window.get_size().width == width
    assert window.menu_layer.get_allocation().height == window.overlay.get_allocation().height
    assert window.menu_revealer.translate_coordinates(window.overlay, 0, 0) == (0, 0)
    assert not window.workspace.get_sensitive()
    event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    event.button = 1
    window.menu_shade.emit('button-press-event', event)
    wait_until(lambda: not window.menu_layer.get_visible())
    assert window.workspace.get_sensitive()
    assert window.get_visible()
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
