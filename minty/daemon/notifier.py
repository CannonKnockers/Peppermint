"""Desktop notifications through libnotify."""

from __future__ import annotations

import logging
import shutil
import subprocess

from minty import config

log = logging.getLogger("minty.notifier")

_ready = False
_Notify = None


def _init() -> bool:
    global _ready, _Notify
    if _ready:
        return True
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify

        Notify.init(config.APP_NAME)
        _Notify = Notify
        _ready = True
    except Exception as exc:  # a missing display must not stop the daemon
        log.warning("libnotify is not available: %s", exc)
        _ready = False
    return _ready


def send(title: str, body: str, urgent: bool = False, action_label: str = "") -> None:
    """Show a notification. A click on the action opens the Minty window."""
    if _init() and _Notify is not None:
        try:
            note = _Notify.Notification.new(title, body, config.APP_ICON)
            if urgent:
                note.set_urgency(_Notify.Urgency.CRITICAL)
            if action_label:
                note.add_action("open", action_label, _on_open, None)
            note.show()
            return
        except Exception as exc:
            log.warning("The notification failed: %s", exc)

    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", config.APP_NAME, title, body],
                       capture_output=True, timeout=10)


def _on_open(notification, action, user_data):
    """Open the Minty window when the user clicks the notification."""
    try:
        from minty.common import dbus_api

        dbus_api.call_window("Toggle")
    except Exception as exc:
        log.warning("Minty could not open the window: %s", exc)
