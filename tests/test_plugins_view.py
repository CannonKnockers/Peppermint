"""Plugin management uses temporary state and mocked D-Bus actions."""
import json
import threading
import time

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk
from peppermint.ui.plugins_view import PluginsView


def wait(view):
    deadline = time.monotonic() + 3
    while view.busy and time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        time.sleep(.005)
    assert not view.busy


@pytest.fixture
def create_view():
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')
    views = []
    def create(call):
        view = PluginsView(call=call)
        views.append(view)
        view.show_all()
        view.reload()
        wait(view)
        return view
    yield create
    for view in views:
        view.destroy()


def test_enable_disable_reads_saved_state_off_gtk(create_view):
    row = dict(name='sample', enabled=False, loaded=False, tools=[], error='Old crash')
    calls = []
    def call(method, params=None, *_args, **kwargs):
        calls.append(method)
        assert threading.get_ident() != main_thread
        if method == 'ListPlugins':
            return GLib.Variant('(s)', (json.dumps([row]),))
        assert params.unpack() == ('sample',)
        row.update(enabled=method == 'EnablePlugin', loaded=method == 'EnablePlugin', error='')
    main_thread = threading.get_ident()
    view = create_view(call)
    assert view.buttons['sample'].get_label() == 'Enable'
    view.buttons['sample'].clicked()
    wait(view)
    assert view.buttons['sample'].get_label() == 'Disable'
    view.buttons['sample'].clicked()
    wait(view)
    assert view.buttons['sample'].get_label() == 'Enable'
    assert calls == ['ListPlugins', 'EnablePlugin', 'ListPlugins', 'DisablePlugin', 'ListPlugins']


def test_failed_enable_displays_error_and_keeps_enable(create_view):
    def call(method, *args, **kwargs):
        if method != 'ListPlugins':
            raise RuntimeError('Plugin failed to load')
        return GLib.Variant('(s)', (json.dumps([dict(name='broken', enabled=False, loaded=False, error='SyntaxError')]),))
    view = create_view(call)
    view.buttons['broken'].clicked()
    wait(view)
    assert 'failed to load' in view.notice.get_text()
    assert view.buttons['broken'].get_label() == 'Enable'
    labels = [child.get_text() for child in view.cards.get_children()[0].get_children() if isinstance(child, Gtk.Label)]
    assert 'Last failure: SyntaxError' in labels


def test_offline_disables_stale_actions_then_refresh_recovers(create_view):
    offline = False
    def call(*args, **kwargs):
        if offline:
            raise RuntimeError('Offline')
        return GLib.Variant('(s)', ('[{"name":"sample","enabled":true,"loaded":true}]',))
    view = create_view(call)
    offline = True
    view.reload()
    wait(view)
    assert not view.buttons['sample'].get_sensitive()
    assert view.refresh_button.get_sensitive()
    offline = False
    view.refresh_button.clicked()
    wait(view)
    assert view.buttons['sample'].get_sensitive()


def test_empty_and_destroyed_view(create_view):
    view = create_view(lambda *args, **kwargs: GLib.Variant('(s)', ('[]',)))
    assert 'No plugins installed' in view.notice.get_text()
    view.destroy()
    assert view._finish([], []) == GLib.SOURCE_REMOVE


def test_duplicate_action_is_ignored(create_view):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def call(method, *args, **kwargs):
        calls.append(method)
        if method == 'EnablePlugin':
            entered.set()
            assert release.wait(3)
            return None
        return GLib.Variant('(s)', ('[{"name":"sample","enabled":false,"loaded":false}]',))
    view = create_view(call)
    view.buttons['sample'].clicked()
    try:
        assert entered.wait(1)
        view._request('EnablePlugin', 'sample')
        view.reload()
        assert not view.buttons['sample'].get_sensitive()
    finally:
        release.set()
    wait(view)
    assert calls.count('EnablePlugin') == 1
