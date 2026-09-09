"""Single-worker sampling lifecycle. This module never imports GTK."""

from collections import deque
from copy import deepcopy
import threading
import time

from peppermint.diagnostics.sampler import LinuxSampler, SamplingStopped


class Monitor:
    """Callbacks run on the worker and must be quick/nonblocking.

    stop(wait=False) suppresses future callbacks and signals cancellation of the
    standard sampler between reads. It does not wait for a current probe; its
    bool return says whether the worker has exited. It may wait for a callback
    already executing. wait=True additionally joins up to timeout seconds. Start
    refuses while an earlier worker exits. GTK callers must invalidate callbacks
    they already queued with GLib.idle_add when hiding or pausing the view.
    """

    def __init__(self, on_sample, sampler=None, interval=2.0, max_samples=150, *, on_error=None):
        if not callable(on_sample):
            raise TypeError('on_sample must be callable')
        if on_error is not None and not callable(on_error):
            raise TypeError('on_error must be callable or None')
        self.on_sample = on_sample
        self.on_error = on_error
        self.sampler = sampler or LinuxSampler()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._history = deque(maxlen=max(1, min(int(max_samples), 10000)))
        self.last_error = None
        self.set_interval(interval)

    def set_interval(self, seconds):
        seconds = float(seconds)
        if not 0.1 <= seconds <= 60:
            raise ValueError('Sampling interval must be between 0.1 and 60 seconds.')
        with self._lock:
            self.interval = seconds
            self._wake.set()

    @property
    def running(self):
        with self._lock:
            return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def snapshot_history(self):
        """Return bounded metric history; process inventories are current only."""
        with self._lock:
            return deepcopy(list(self._history))

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._wake.clear()
            reset = getattr(self.sampler, 'reset', None)
            if reset:
                reset()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name='peppermint-diagnostics', daemon=True)
            self._thread.start()
            return True

    def stop(self, wait=False, timeout=None):
        with self._lock:
            self._stop.set()
            self._wake.set()
            worker = self._thread
        if wait and worker and worker is not threading.current_thread():
            worker.join(timeout)
        return not worker or not worker.is_alive()

    def _report_error(self, prefix, exc):
        with self._lock:
            self.last_error = f'{prefix}: {type(exc).__name__[:100]}'
            if not self._stop.is_set() and self.on_error is not None:
                try:
                    self.on_error(self.last_error)
                except Exception:
                    # Keep the original failure; error reporting must not recurse.
                    pass

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    sample = self.sampler.sample(cancelled=self._stop.is_set) if isinstance(self.sampler, LinuxSampler) else self.sampler.sample()
                except SamplingStopped:
                    break
                except Exception as exc:
                    self._report_error('Sampling failed', exc)
                    break
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._history.append(deepcopy({key: value for key, value in sample.items()
                                                   if key != 'processes'}))
                    try:
                        self.on_sample(deepcopy(sample))
                    except Exception as exc:
                        self._report_error('Sample callback failed', exc)
                        break
                completed = time.monotonic()
                while not self._stop.is_set():
                    with self._lock:
                        remaining = self.interval - (time.monotonic() - completed)
                    if remaining <= 0:
                        break
                    self._wake.wait(remaining)
                    self._wake.clear()
        finally:
            with self._lock:
                self._stop.set()
