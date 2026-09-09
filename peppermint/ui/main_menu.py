"""The window's single navigation menu, opened by the peppermint emblem."""

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


PAGES = (
    ('tasks', 'Tasks', 'view-list-symbolic'),
    ('conversations', 'Conversations', 'user-available-symbolic'),
    ('diagnostics', 'Diagnostics', 'utilities-system-monitor-symbolic'),
    ('manual', 'User manual', 'help-browser-symbolic'),
)


class MainMenu(Gtk.Box):
    """Full-height navigation content for the sliding left drawer."""

    def __init__(self, on_navigate, on_new, on_refresh, on_hide, on_close, on_recovery=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class('peppermint-menu')
        self.set_size_request(264, -1)
        self._on_close = on_close
        heading_row = Gtk.Box(spacing=8)
        heading = Gtk.Label(label='Peppermint', xalign=0)
        heading.get_style_context().add_class('menu-heading')
        heading_row.pack_start(heading, True, True, 0)
        self.close_button = Gtk.Button.new_from_icon_name('go-previous-symbolic', Gtk.IconSize.MENU)
        self.close_button.get_style_context().add_class('sidebar-close')
        self.close_button.set_tooltip_text('Close sidebar')
        self.close_button.get_accessible().set_name('Close sidebar')
        self.close_button.connect('clicked', lambda *_: on_close())
        heading_row.pack_end(self.close_button, False, False, 0)
        self.pack_start(heading_row, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.pack_start(scroller, True, True, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.add(content)

        self.navigation = {}
        for name, title, icon in PAGES:
            button = self._button(title, icon, lambda page=name: on_navigate(page))
            self.navigation[name] = button
            content.pack_start(button, False, False, 0)
        if on_recovery:
            self.recovery_button = self._button('Recovery', 'system-run-symbolic', on_recovery)
            self.recovery_button.set_tooltip_text('Recovery and session controls · Ctrl+Alt+Delete')
            content.pack_start(self.recovery_button, False, False, 0)

        content.pack_start(Gtk.Separator(), False, False, 6)
        self.new_button = self._button('New conversation', 'list-add-symbolic', on_new)
        self.refresh_button = self._button('Refresh', 'view-refresh-symbolic', on_refresh)
        self.hide_button = self._button('Hide window', 'window-minimize-symbolic', on_hide)
        for button in (self.new_button, self.refresh_button):
            content.pack_start(button, False, False, 0)

        self.pack_start(self.hide_button, False, False, 0)

        self.status = Gtk.Label(label='Connecting', xalign=0)
        self.status.get_style_context().add_class('menu-status')
        self.pack_start(self.status, False, False, 0)
        self.set_page('tasks')
        self.show_all()

    def _button(self, title, icon, callback):
        button = Gtk.Button()
        button.get_style_context().add_class('menu-item')
        button.get_accessible().set_name(title)
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
        row.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)
        button.add(row)
        def activate(_button):
            self._on_close()
            callback()
        button.connect('clicked', activate)
        return button

    def set_page(self, page):
        for name, button in self.navigation.items():
            style = button.get_style_context()
            if name == page:
                style.add_class('menu-current')
            else:
                style.remove_class('menu-current')
