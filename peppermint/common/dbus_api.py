"""The D-Bus interface that joins the daemon, the CLI, and the window."""

from __future__ import annotations

from gi.repository import Gio, GLib

DAEMON_NAME = "org.peppermint.Daemon"
DAEMON_PATH = "/org/peppermint/Daemon"
DAEMON_IFACE = "org.peppermint.Daemon"

WINDOW_NAME = "org.peppermint.Window"
WINDOW_PATH = "/org/peppermint/Window"
WINDOW_IFACE = "org.peppermint.Window"

RETEST_OUTCOMES = ("passed", "failed", "not_tested")

DAEMON_XML = """
<node>
  <interface name="org.peppermint.Daemon">
    <method name="AddTask">
      <arg type="s" name="idea" direction="in"/>
      <arg type="i" name="id" direction="out"/>
    </method>
    <method name="ListTasks">
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="TaskOverview">
      <arg type="s" name="status_filter" direction="in"/>
      <arg type="s" name="query" direction="in"/>
      <arg type="i" name="offset" direction="in"/>
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="GetTask">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="Confirm">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="approved" direction="in"/>
    </method>
    <method name="ConfirmAction">
      <arg type="i" name="id" direction="in"/>
      <arg type="i" name="confirmation_id" direction="in"/>
      <arg type="b" name="approved" direction="in"/>
    </method>
    <method name="Answer">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="Retest">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="request_id" direction="in"/>
      <arg type="s" name="outcome" direction="in"/>
    </method>
    <method name="Chat">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="Cancel">
      <arg type="i" name="id" direction="in"/>
    </method>
    <method name="Undo">
      <arg type="i" name="limit" direction="in"/>
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="Revert">
      <arg type="i" name="undo_id" direction="in"/>
      <arg type="i" name="task_id" direction="in"/>
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="Health">
      <arg type="s" name="json" direction="out"/>
    </method>
    <signal name="TaskUpdated">
      <arg type="i" name="id"/>
      <arg type="s" name="status"/>
    </signal>
  </interface>
</node>
"""

WINDOW_XML = """
<node>
  <interface name="org.peppermint.Window">
    <method name="Toggle"/>
    <method name="Present"/>
    <method name="OpenTask">
      <arg type="i" name="id" direction="in"/>
    </method>
  </interface>
</node>
"""


class DaemonNotRunning(Exception):
    """The daemon does not own its bus name."""


def session_bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _call(name: str, path: str, iface: str, method: str, params, reply_type, timeout=120000):
    bus = session_bus()
    try:
        result = bus.call_sync(
            name, path, iface, method,
            params, reply_type, Gio.DBusCallFlags.NONE, timeout, None,
        )
    except GLib.Error as err:
        if "ServiceUnknown" in err.message or "was not provided" in err.message:
            raise DaemonNotRunning(
                f"`{name}` is not running. Start it with: systemctl --user start peppermint-daemon"
            ) from err
        raise
    return result


def call_daemon(method: str, params=None, reply_type=None, timeout=120000):
    return _call(DAEMON_NAME, DAEMON_PATH, DAEMON_IFACE, method, params, reply_type, timeout)


def call_window(method: str, params=None, reply_type=None, timeout=10000):
    return _call(WINDOW_NAME, WINDOW_PATH, WINDOW_IFACE, method, params, reply_type, timeout)


def name_has_owner(name: str) -> bool:
    bus = session_bus()
    try:
        result = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 5000, None,
        )
    except GLib.Error:
        return False
    return bool(result.unpack()[0])
