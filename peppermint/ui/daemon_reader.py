"""Coalesced read-only D-Bus requests, away from the GTK thread."""

from collections import OrderedDict
import json
import logging
import threading

from gi.repository import GLib

from peppermint.common import dbus_api

log = logging.getLogger("peppermint.ui.reads")


class DaemonReader:
    """One bounded worker; newer requests replace older requests for each key.

    Callbacks run through ``dispatch`` (GLib by default). Closing or superseding
    a request invalidates even a completion already waiting in the GTK queue.
    """

    def __init__(self, call=None, dispatch=None):
        self._call = call or dbus_api.call_daemon
        self._dispatch = dispatch or GLib.idle_add
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._completed = OrderedDict()
        self._drain_scheduled = False
        self._latest = {}
        self._serial = 0
        self._closed = False
        self._worker = threading.Thread(target=self._run, daemon=True, name="peppermint-ui-reads")
        self._worker.start()

    def request(self, key, method, params, callback):
        with self._condition:
            if self._closed:
                return
            self._serial += 1
            version = self._serial
            self._latest[key] = version
            self._completed.pop(key, None)
            self._pending[key] = (version, method, params, callback)
            # Only visible task details and two overview requests are expected.
            # Drop the oldest waiting read if a burst exceeds the UI's bound.
            if len(self._pending) > 128:
                dropped, _ = self._pending.popitem(last=False)
                self._latest.pop(dropped, None)
            self._condition.notify()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._completed.clear()
            self._latest.clear()
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                key, (version, method, params, callback) = self._pending.popitem(last=False)
            payload = error = None
            try:
                result = self._call(method, params, GLib.VariantType("(s)"), timeout=3000)
                payload = json.loads(result.unpack()[0])
            except Exception as exc:
                error = exc
            with self._condition:
                if self._closed or self._latest.get(key) != version:
                    continue
                self._completed[key] = (version, callback, payload, error)
                if len(self._completed) > 128:
                    dropped, (dropped_version, *_) = self._completed.popitem(last=False)
                    if self._latest.get(dropped) == dropped_version:
                        self._latest.pop(dropped, None)
                schedule = not self._drain_scheduled
                self._drain_scheduled = True
            if schedule:
                self._dispatch(self._drain)

    def _drain(self):
        """Use one GTK idle source, delivering one bounded result per iteration."""
        with self._condition:
            if self._closed or not self._completed:
                self._drain_scheduled = False
                return GLib.SOURCE_REMOVE
            key, (version, callback, payload, error) = self._completed.popitem(last=False)
        try:
            self._deliver(key, version, callback, payload, error)
        except Exception:
            log.exception("Could not display a daemon read result")
        with self._condition:
            if self._closed or not self._completed:
                self._drain_scheduled = False
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

    def _deliver(self, key, version, callback, payload, error):
        with self._condition:
            if self._closed or self._latest.get(key) != version:
                return GLib.SOURCE_REMOVE
            self._latest.pop(key, None)
        callback(payload, error)
        return GLib.SOURCE_REMOVE
