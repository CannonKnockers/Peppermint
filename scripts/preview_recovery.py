#!/usr/bin/env python3
"""Capture recovery with illustrative processes and every action replaced."""

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1100)
    parser.add_argument('--height', type=int, default=900)
    args = parser.parse_args()
    import cairo
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk, GLib, Gtk
    from peppermint.recovery.app import RecoveryWindow
    from peppermint.recovery import admin
    if not Gtk.init_check()[0]:
        parser.error('A GTK display is required.')
    rows = [dict(pid=pid, start_ticks=10000 + pid, uid=1000, name=name, state='S', rss_bytes=rss * 1024 ** 2)
            for pid, name, rss in ((2450, 'Example frozen game', 2100), (1800, 'Example browser', 1200),
                                   (3250, 'Example editor', 220))]
    def forbidden(*_args, **_kwargs):
        return {'status': 'denied', 'message': 'Illustrative preview; actions are disabled.'}
    errors = []
    with patch.object(admin, 'helper_ready', return_value=True), patch.object(admin, 'run_helper', side_effect=forbidden):
        window = RecoveryWindow(list_processes=lambda: rows, act=forbidden, admin_act=forbidden,
                                session_capabilities=lambda: {key: key != 'hibernate' for key in
                                  ('lock', 'switch_user', 'logout', 'suspend', 'hibernate', 'restart', 'shutdown')},
                                session_request=forbidden)
        window.set_default_size(args.width, args.height)
        window.set_keep_above(False)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.show_all()
        def capture():
            try:
                width, height = window.get_allocated_width(), window.get_allocated_height()
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                cr = cairo.Context(surface)
                window.draw(cr)
                for column in window.tree.get_columns():
                    button = column.get_button()
                    origin = button.translate_coordinates(window, 0, 0) if button else None
                    if origin and button.get_mapped():
                        cr.save()
                        cr.translate(*origin)
                        button.draw(cr)
                        cr.restore()
                output = args.output.expanduser().resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
                pixbuf.savev(str(output), 'png', [], [])
                print(f'Saved recovery preview: {output} ({width} × {height})')
            except Exception as exc:
                errors.append(exc)
            finally:
                window.destroy()
                Gtk.main_quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(250, lambda: (window.status.set_text('Preview · illustrative processes · no recovery action has run'),
                                      GLib.SOURCE_REMOVE)[1])
        GLib.timeout_add(500, capture)
        Gtk.main()
    if errors:
        raise errors[0]
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
