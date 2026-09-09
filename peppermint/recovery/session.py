"""Bounded requests to Linux Mint's existing session and power services.

The recovery UI confirms disruptive actions before calling ``request``.
Logout, restart and shutdown additionally use Mint's normal confirmation and
inhibitor handling. A successful reply acknowledges a request, not completion.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

TIMEOUT_MS = 3000
SESSION_NAME = "org.gnome.SessionManager"
SESSION_PATH = "/org/gnome/SessionManager"
DIALOG_INTERFACE = "org.cinnamon.SessionManager.EndSessionDialog"
SCREEN_NAME = "org.cinnamon.ScreenSaver"
SCREEN_PATH = "/org/cinnamon/ScreenSaver"
LOGIN_NAME = "org.freedesktop.login1"
LOGIN_PATH = "/org/freedesktop/login1"
LOGIN_INTERFACE = "org.freedesktop.login1.Manager"
DM_TOOL = "/usr/bin/dm-tool"
ACTIONS = ("lock", "switch_user", "logout", "restart", "shutdown", "suspend", "hibernate")


class GioSessionBackend:
    """Direct DBus calls avoid unbounded proxy initialization/introspection."""

    def __init__(self):
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib

    def call(self, *, bus, destination, path, interface, method, signature=None,
             arguments=(), timeout_ms=TIMEOUT_MS, auto_start=False):
        cancellable = self.Gio.Cancellable.new()
        # Cover connecting/authenticating to the bus as well as its method call.
        timer = threading.Timer(timeout_ms / 1000, cancellable.cancel)
        timer.daemon = True
        timer.start()
        try:
            connection = self.Gio.bus_get_sync(
                self.Gio.BusType.SYSTEM if bus == "system" else self.Gio.BusType.SESSION,
                cancellable,
            )
            parameters = self.GLib.Variant(signature, arguments) if signature else None
            result = connection.call_sync(
                destination, path, interface, method, parameters, None,
                self.Gio.DBusCallFlags.NONE if auto_start else self.Gio.DBusCallFlags.NO_AUTO_START,
                timeout_ms, cancellable,
            )
            return result.unpack() if result is not None else ()
        finally:
            timer.cancel()

    def executable(self, path):
        return os.path.isfile(path) and os.access(path, os.X_OK)

    def lock_allowed(self):
        source = self.Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.cinnamon.desktop.lockdown", True) if source else None
        if schema is None or not schema.has_key("disable-lock-screen"):
            return False
        return not self.Gio.Settings.new_full(schema, None, None).get_boolean("disable-lock-screen")

    def run(self, arguments, *, timeout_ms=TIMEOUT_MS):
        result = subprocess.run(arguments, shell=False, check=False, capture_output=True,
                                text=True, timeout=timeout_ms / 1000)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:300] or "The display manager refused the request.")


def _remaining(deadline):
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise TimeoutError("The desktop did not reply within three seconds.")
    return min(TIMEOUT_MS, remaining)


def _call(backend, deadline, destination, path, interface, method, *, bus="session",
          signature=None, arguments=(), auto_start=False):
    return backend.call(bus=bus, destination=destination, path=path, interface=interface,
                        method=method, signature=signature, arguments=arguments,
                        timeout_ms=_remaining(deadline), auto_start=auto_start)


def _capabilities(backend, deadline):
    available = dict.fromkeys(ACTIONS, False)
    try:
        response = _call(backend, deadline, SESSION_NAME, SESSION_PATH,
                         DIALOG_INTERFACE, "GetCapabilities")
        values = response[0]
        if len(values) != 7 or not all(isinstance(value, bool) for value in values):
            raise ValueError("Unexpected session capability response")
        switch, shutdown, restart, _hybrid_sleep, suspend, hibernate, logout = values
        available.update(switch_user=switch, shutdown=shutdown, restart=restart,
                         suspend=suspend, hibernate=hibernate, logout=logout)
    except Exception:
        # Missing, stalled or incompatible session services are unavailable.
        pass
    try:
        response = _call(backend, deadline, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                         "org.freedesktop.DBus", "NameHasOwner", signature="(s)",
                         arguments=(SCREEN_NAME,))
        screen_available = response == (True,)
        if not screen_available:
            # Cinnamon's screensaver exits when idle; merely checking for a
            # running owner would hide a normally functioning Lock action.
            response = _call(backend, deadline, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "ListActivatableNames")
            screen_available = SCREEN_NAME in response[0]
        available["lock"] = screen_available and backend.lock_allowed()
    except Exception:
        pass
    available["switch_user"] = bool(available["switch_user"] and available["lock"] and
                                    backend.executable(DM_TOOL))
    return available


def capabilities(backend=None) -> dict[str, bool]:
    """Read current support without issuing any session-changing operation."""
    try:
        return _capabilities(backend or GioSessionBackend(), time.monotonic() + TIMEOUT_MS / 1000)
    except Exception:
        return dict.fromkeys(ACTIONS, False)


def request(action: str, backend=None) -> dict[str, str]:
    """Request one allowlisted action after the recovery UI's confirmation."""
    if action not in ACTIONS:
        return {"status": "unsupported", "message": "Unknown session action."}
    labels = {"lock": "Screen lock", "switch_user": "User switching", "logout": "Log out",
              "restart": "Restart", "shutdown": "Shut down", "suspend": "Suspend", "hibernate": "Hibernate"}
    label = labels[action]
    action_started = False
    deadline = time.monotonic() + TIMEOUT_MS / 1000
    try:
        backend = backend or GioSessionBackend()
        if not _capabilities(backend, deadline)[action]:
            return {"status": "unavailable", "message": f"{label} is unavailable in the current desktop session."}
        action_started = True
        if action in ("lock", "switch_user"):
            _call(backend, deadline, SCREEN_NAME, SCREEN_PATH, SCREEN_NAME, "Lock",
                  signature="(s)", arguments=("",), auto_start=True)
            if action == "switch_user":
                backend.run([DM_TOOL, "switch-to-greeter"], timeout_ms=_remaining(deadline))
        elif action in ("logout", "restart", "shutdown"):
            methods = {"logout": "Logout", "restart": "Reboot", "shutdown": "Shutdown"}
            _call(backend, deadline, SESSION_NAME, SESSION_PATH, SESSION_NAME, methods[action],
                  signature="(u)" if action == "logout" else None,
                  arguments=(0,) if action == "logout" else ())
        else:
            _call(backend, deadline, LOGIN_NAME, LOGIN_PATH, LOGIN_INTERFACE,
                  "Suspend" if action == "suspend" else "Hibernate", bus="system",
                  signature="(b)", arguments=(True,))
    except Exception as error:
        detail = str(error).strip()[:300]
        if action_started and (isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or
                               any(word in detail.lower() for word in ("timed out", "timeout", "cancelled", "canceled"))):
            return {"status": "unknown", "message": f"{label} received no reply in time. The request may still be pending; check the desktop before trying again."}
        return {"status": "error", "message": f"{label} could not be requested: {detail or 'desktop service unavailable'}"}
    if action in ("logout", "restart", "shutdown"):
        message = f"{label} requested. Complete Linux Mint's confirmation; applications can still block the action."
    else:
        message = f"{label} requested. The desktop service will handle the action."
    return {"status": "requested", "message": message}
