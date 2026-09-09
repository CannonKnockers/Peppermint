"""Independent fullscreen recovery window; no daemon, AI, or conversation reads."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from peppermint.recovery import admin, processes

log = logging.getLogger('peppermint.recovery')


def text(value, style=None):
    label = Gtk.Label(label=value, xalign=0)
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        label.get_style_context().add_class(style)
    return label


class RecoveryWindow(Gtk.ApplicationWindow):
    def __init__(self, app=None, *, list_processes=None, act=None, admin_act=None,
                 session_capabilities=None, session_request=None):
        super().__init__(application=app, title='Peppermint Recovery')
        self.set_default_size(1000, 740)
        self.set_size_request(660, 560)
        self.set_keep_above(True)
        self.get_style_context().add_class('peppermint-window')
        self._list = list_processes or processes.list_processes
        self._act = act or processes.act
        self._admin_act = admin_act or admin.act_as_admin
        self._session_capabilities = session_capabilities
        self._session_request = session_request
        self._capabilities = {}
        self._closed = threading.Event()
        self._busy = False
        self._rows = {}
        self._selected = None
        self._force_allowed = set()
        self._loading = False
        self._worker = None
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).parents[1] / 'ui' / 'theme.css'))
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._build()
        self.connect('key-press-event', self._key)
        self.connect('destroy', lambda *_: self._closed.set())
        self.refresh()
        self._load_session_controls()

    def _build(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for edge in ('start', 'end', 'top', 'bottom'):
            getattr(content, f'set_margin_{edge}')(24)
        content.set_vexpand(True)
        page = Gtk.ScrolledWindow()
        page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page.add(content)
        self.add(page)
        heading = Gtk.Box(spacing=16)
        heading.pack_start(text('Peppermint Recovery', 'hero-title'), True, True, 0)
        self.back = Gtk.Button(label='Return to desktop · Esc')
        self.back.connect('clicked', lambda *_: self.destroy())
        heading.pack_end(self.back, False, False, 0)
        content.pack_start(heading, False, False, 0)
        content.pack_start(text('Ctrl+Alt+Delete · Select a frozen process, request a stop, then force it only if needed.', 'muted'), False, False, 0)
        content.pack_start(text('Runs separately from the main window and AI. The desktop and keyboard service must still respond.', 'muted'), False, False, 0)

        controls = Gtk.Box(spacing=12)
        self.search = Gtk.SearchEntry(placeholder_text='Find a process by name or PID')
        self.search.get_accessible().set_name('Search recovery processes')
        self.search.connect('search-changed', lambda *_: self.filtered.refilter())
        controls.pack_start(self.search, True, True, 0)
        self.refresh_button = Gtk.Button(label='Refresh processes')
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        controls.pack_end(self.refresh_button, False, False, 0)
        content.pack_start(controls, False, False, 0)

        self.store = Gtk.ListStore(str, str, str, str, str, str)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self._visible)
        self.tree = Gtk.TreeView(model=self.filtered)
        self.tree.get_accessible().set_name('Recovery process list')
        for index, title in enumerate(('PID', 'Process', 'Owner UID', 'State', 'Memory MiB')):
            renderer = Gtk.CellRendererText()
            if index == 1:
                renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(index == 1)
            self.tree.append_column(column)
        self.tree.get_selection().connect('changed', self._selection_changed)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(140)
        scroller.add(self.tree)
        content.pack_start(scroller, True, True, 0)
        self.detail = text('Select the exact process you want to stop.')
        content.pack_start(self.detail, False, False, 0)
        self.status = text('Reading processes…', 'muted')
        content.pack_start(self.status, False, False, 0)
        actions = Gtk.Box(spacing=12)
        self.stop_button = Gtk.Button(label='Request stop')
        self.stop_button.get_style_context().add_class('suggested-action')
        self.stop_button.connect('clicked', lambda *_: self._confirm_action('terminate'))
        self.force_button = Gtk.Button(label='Force stop…')
        self.force_button.get_style_context().add_class('stop-button')
        self.force_button.connect('clicked', lambda *_: self._confirm_action('kill'))
        actions.pack_start(self.stop_button, False, False, 0)
        actions.pack_start(self.force_button, False, False, 0)
        self.admin_button = Gtk.Button(label='Check administrator access')
        self.admin_button.set_sensitive(admin.helper_ready())
        self.admin_button.connect('clicked', lambda *_: self._check_admin())
        actions.pack_end(self.admin_button, False, False, 0)
        content.pack_start(actions, False, False, 0)
        self.admin_status = text('Administrator helper ready; protected actions ask for your password.' if admin.helper_ready()
                                 else 'Administrator helper not installed. Your own processes can still be stopped.', 'muted')
        content.pack_start(self.admin_status, False, False, 0)
        content.pack_start(Gtk.Separator(), False, False, 0)
        content.pack_start(text('Session controls', 'task-title'), False, False, 0)
        session_buttons = Gtk.FlowBox()
        session_buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        session_buttons.set_min_children_per_line(2)
        session_buttons.set_max_children_per_line(7)
        session_buttons.set_column_spacing(8)
        session_buttons.set_row_spacing(6)
        self.session_buttons = {}
        for action, title in (('lock', 'Lock screen'), ('switch_user', 'Switch user'),
                              ('logout', 'Log out…'), ('suspend', 'Suspend…'),
                              ('hibernate', 'Hibernate…'), ('restart', 'Restart…'),
                              ('shutdown', 'Shut down…')):
            button = Gtk.Button(label=title)
            button.set_sensitive(False)
            button.connect('clicked', lambda _button, name=action: self._session_action(name))
            self.session_buttons[action] = button
            session_buttons.add(button)
        content.pack_start(session_buttons, False, False, 0)
        self.session_status = text('Checking the session controls supported by Mint…', 'muted')
        content.pack_start(self.session_status, False, False, 0)
        self._update_buttons()

    def _load_session_controls(self):
        def work():
            try:
                if self._session_capabilities is None:
                    from peppermint.recovery import session
                    capabilities = session.capabilities()
                else:
                    capabilities = self._session_capabilities()
            except Exception:
                capabilities = {}
            def deliver():
                if not self._closed.is_set():
                    self._capabilities = capabilities
                    for action, button in self.session_buttons.items():
                        available = capabilities.get(action, False)
                        # Hide unsupported sleep modes; show other unavailable
                        # controls disabled so missing services stay explicit.
                        if action in ('suspend', 'hibernate'):
                            button.get_parent().set_no_show_all(not available)
                            button.get_parent().set_visible(available)
                    self.session_status.set_text('Mint handles confirmation and applications that need attention.' if any(capabilities.values())
                                                 else 'Session controls unavailable; process recovery is still available.')
                    self._update_buttons()
                return GLib.SOURCE_REMOVE
            if not self._closed.is_set():
                GLib.idle_add(deliver)
        threading.Thread(target=work, daemon=True, name='peppermint-session-controls').start()

    def _session_action(self, action):
        if self._busy or not self._capabilities.get(action):
            return
        if action in ('suspend', 'hibernate', 'switch_user', 'lock'):
            titles = {'suspend': 'Suspend this computer?', 'hibernate': 'Hibernate this computer?',
                      'switch_user': 'Switch to the login screen?', 'lock': 'Lock this screen?'}
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.CANCEL, text=titles[action])
            dialog.add_button('Continue', Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK or self._closed.is_set():
                return
        # Mint owns the confirmation/inhibitor dialog for logout/restart/poweroff.
        # Yield the screen so it and the desktop's authentication agent can show.
        self.set_keep_above(False)
        self.hide()
        def operation():
            if self._closed.is_set():
                return {'status': 'cancelled', 'message': 'Session request cancelled.'}
            if self._session_request is None:
                from peppermint.recovery import session
                return session.request(action)
            return self._session_request(action)
        def completed(result, error):
            # A timed-out dispatch may already have opened Mint's dialog.
            # Keep yielding the screen until explicitly reopened by the user.
            if error or result.get('status') not in ('requested', 'unknown'):
                self.show_all()
                self.fullscreen()
                self.set_keep_above(True)
                self.present()
            self.session_status.set_text(error or result.get('message', 'No session result returned.'))
        self._work(operation, completed)

    def _visible(self, model, itr, _data):
        query = self.search.get_text().strip().casefold()
        return not query or query in (model[itr][0] + ' ' + model[itr][1]).casefold()

    @staticmethod
    def _identity(row):
        return f"{row['pid']}:{row['start_ticks']}:{row['uid']}"

    def _selection_changed(self, selection):
        if self._loading:
            return
        model, itr = selection.get_selected()
        self._selected = model[itr][5] if itr is not None else None
        row = self._rows.get(self._selected)
        if row:
            self.detail.set_text(f"{row['name']} · PID {row['pid']} · owner UID {row['uid']}\n"
                                 + (row.get('protected_reason', 'This core system process is protected.') if row.get('protected') else
                                    'Request stop first. Force stop is enabled only if that process remains running.'))
        else:
            self.detail.set_text('Select the exact process you want to stop.')
        self._update_buttons()

    def _update_buttons(self):
        target = self._rows.get(self._selected)
        selected = target is not None and not target.get('protected')
        self.stop_button.set_sensitive(selected and not self._busy)
        self.force_button.set_sensitive(selected and self._selected in self._force_allowed and not self._busy)
        self.refresh_button.set_sensitive(not self._busy)
        self.admin_button.set_sensitive(not self._busy and admin.helper_ready())
        self.tree.set_sensitive(not self._busy)
        for action, button in self.session_buttons.items():
            button.set_sensitive(not self._busy and self._capabilities.get(action, False))

    def _work(self, operation, completed):
        if self._busy or self._closed.is_set():
            return
        self._busy = True
        self._update_buttons()
        def work():
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc)
            if not self._closed.is_set():
                GLib.idle_add(deliver, result, error)
        def deliver(result, error):
            if not self._closed.is_set():
                self._busy = False
                completed(result, error)
                self._update_buttons()
            return GLib.SOURCE_REMOVE
        self._worker = threading.Thread(target=work, daemon=True, name='peppermint-recovery')
        self._worker.start()

    def refresh(self):
        self._work(self._list, self._accept_processes)

    def _accept_processes(self, rows, error):
        if error:
            self.status.set_text('Could not read processes: ' + error)
            return
        selected = self._selected
        self._loading = True
        self._rows = {self._identity(row): row for row in rows}
        self._force_allowed.intersection_update(self._rows)
        self.store.clear()
        for identity, row in self._rows.items():
            self.store.append((str(row['pid']), row['name'], str(row['uid']), row['state'],
                               f"{row['rss_bytes'] / 1024 ** 2:.1f}" if row.get('rss_bytes') is not None else '—', identity))
        self._loading = False
        for row in self.filtered:
            if row[5] == selected:
                self.tree.get_selection().select_iter(row.iter)
                break
        self._selection_changed(self.tree.get_selection())
        self.status.set_text(f'{len(rows)} processes in this bounded snapshot · Refresh to update')

    def _confirm_action(self, action):
        target = self._rows.get(self._selected)
        if self._busy or not target or target.get('protected') or (action == 'kill' and self._selected not in self._force_allowed):
            return
        target = dict(target)
        phrase = 'Force stop' if action == 'kill' else 'Request stop for'
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.CANCEL, text=f"{phrase} {target['name']} (PID {target['pid']})?")
        dialog.format_secondary_text('Unsaved work in this process may be lost. This applies only to the selected process. '
                                     + ('It will be forcibly terminated.' if action == 'kill' else 'It will receive a termination request.'))
        dialog.add_button('Force stop' if action == 'kill' else 'Request stop', Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        answer = dialog.run()
        dialog.destroy()
        if answer != Gtk.ResponseType.OK or self._closed.is_set():
            return
        self.status.set_text('Waiting for the selected process…' if target['uid'] == os.getuid()
                             else 'Authenticate in Mint’s administrator dialog to continue…')
        if target['uid'] != os.getuid():
            self.set_keep_above(False)
        def operation():
            if self._closed.is_set():
                return {'status': 'cancelled', 'message': 'Cancelled before sending a stop.'}
            if target['uid'] != os.getuid():
                return self._admin_act(target, action, self._closed.is_set)
            return self._act(target['pid'], target['start_ticks'], target['uid'], action)
        self._work(operation, lambda result, error: self._action_finished(target, action, result, error))

    def _action_finished(self, target, action, result, error):
        self.set_keep_above(True)
        if error:
            self.status.set_text('Stop failed: ' + error)
            return
        identity = self._identity(target)
        if action == 'terminate' and result.get('status') == 'timeout':
            self._force_allowed.add(identity)
        elif result.get('status') == 'exited':
            self._force_allowed.discard(identity)
            self._rows.pop(identity, None)
            for row in self.store:
                if row[5] == identity:
                    self.store.remove(row.iter)
                    break
            self._selection_changed(self.tree.get_selection())
        self.status.set_text(result.get('message', 'No result returned.'))
        log.info('Recovery %s pid=%s status=%s', action, target['pid'], result.get('status'))

    def _check_admin(self):
        self.set_keep_above(False)
        self.admin_status.set_text('Authenticate in Mint’s administrator dialog. This check stops no process.')
        def completed(result, error):
            self.set_keep_above(True)
            ready = not error and result.get('status') == 'ok' and result.get('euid') == 0
            self.admin_status.set_text('Administrator access verified. Each protected action requests authentication.' if ready
                                       else error or result.get('message', 'Administrator check failed.'))
        self._work(lambda: admin.run_helper(['--check'], self._closed.is_set), completed)

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False


class RecoveryApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='org.peppermint.Recovery', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = RecoveryWindow(self)
            self.window.connect('destroy', lambda *_: setattr(self, 'window', None))
        self.window.show_all()
        self.window.fullscreen()
        self.window.set_keep_above(True)
        self.window.present_with_time(Gtk.get_current_event_time() or Gdk.CURRENT_TIME)
        self.window.search.grab_focus()


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    return RecoveryApplication().run(sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
