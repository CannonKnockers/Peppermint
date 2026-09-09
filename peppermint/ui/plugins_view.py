"""Installed plugin management inside the main window."""
import json
import threading

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from peppermint.common import dbus_api


class PluginsView(Gtk.Box):
    def __init__(self, *, call=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._call = call or dbus_api.call_daemon
        self._closed = False
        self.busy = False
        self.buttons = {}
        self.connect('destroy', self._destroy)
        heading = Gtk.Box(spacing=8)
        title = Gtk.Label(label='Plugins', xalign=0)
        title.get_style_context().add_class('hero-title')
        heading.pack_start(title, True, True, 0)
        self.refresh_button = Gtk.Button(label='Refresh plugins')
        self.refresh_button.connect('clicked', lambda *_: self.reload())
        heading.pack_end(self.refresh_button, False, False, 0)
        self.pack_start(heading, False, False, 0)
        self._label(self, 'Manage tools installed in ~/.config/peppermint/tools/. Enabling a plugin loads its Python code. Task actions still use Peppermint’s approval controls.')
        self.notice = self._label(self, 'Open this page to load installed plugins.')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll.add(self.cards)
        self.pack_start(scroll, True, True, 0)

    @staticmethod
    def _label(parent, text):
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        label.set_selectable(True)
        parent.pack_start(label, False, False, 0)
        return label

    def _destroy(self, *_):
        self._closed = True

    def reload(self):
        self._request()

    def _request(self, method=None, name=None):
        if self.busy or self._closed:
            return
        self.busy = True
        self.refresh_button.set_sensitive(False)
        for button in self.buttons.values():
            button.set_sensitive(False)
        self.notice.set_text('Updating plugin…' if method else 'Loading plugins…')

        def work():
            errors, rows = [], None
            if method:
                try:
                    self._call(method, GLib.Variant('(s)', (name,)), timeout=120000)
                except Exception as exc:
                    errors.append(str(exc))
            try:
                result = self._call('ListPlugins', None, GLib.VariantType('(s)'), timeout=10000)
                rows = json.loads(result.unpack()[0])
            except Exception as exc:
                errors.append('Could not load plugins: ' + str(exc))
            GLib.idle_add(self._finish, rows, errors)

        try:
            threading.Thread(target=work, name='peppermint-plugin-controls', daemon=True).start()
        except Exception as exc:
            self._finish(None, [str(exc)])

    def _finish(self, rows, errors):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.busy = False
        self.refresh_button.set_sensitive(True)
        # Leave old actions disabled if the saved state could not be read.
        if rows is not None:
            for child in self.cards.get_children():
                child.destroy()
            self.buttons = {}
            for row in rows:
                self.cards.pack_start(self._card(row), False, False, 0)
            self.cards.show_all()
        message = '\n'.join(errors)
        if not message:
            message = ('No plugins installed. Add a plugin .py file to ~/.config/peppermint/tools/, then refresh.'
                       if not rows else f'{len(rows)} installed plugin(s).')
        self.notice.set_text(message)
        return GLib.SOURCE_REMOVE

    def _card(self, row):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class('overview-card')
        self._label(card, row['name']).get_style_context().add_class('task-title')
        enabled, loaded = row.get('enabled', False), row.get('loaded', False)
        status = 'Enabled' if enabled and loaded else 'Not loaded' if enabled else 'Disabled'
        self._label(card, status)
        tools = row.get('tools') or []
        self._label(card, 'Tools: ' + (', '.join(tools) if tools else 'None loaded'))
        if row.get('error'):
            self._label(card, 'Last failure: ' + row['error'])
        if row.get('path'):
            location = Gtk.Expander(label='Plugin file')
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self._label(box, row['path'])
            location.add(box)
            card.pack_start(location, False, False, 0)
        action = Gtk.Button(label='Disable' if enabled and loaded else 'Enable')
        method = 'DisablePlugin' if enabled and loaded else 'EnablePlugin'
        action.connect('clicked', lambda *_: self._request(method, row['name']))
        card.pack_start(action, False, False, 0)
        self.buttons[row['name']] = action
        return card
