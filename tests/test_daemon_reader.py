"""Concurrent D-Bus reads cannot revive stale views or block GTK callbacks."""

import json
import queue
import threading

import pytest
from gi.repository import GLib

from peppermint.ui.daemon_reader import DaemonReader


def reply(value):
    return GLib.Variant("(s)", (json.dumps(value),))


@pytest.fixture
def reader_factory():
    readers, release_events = [], []

    def make(call):
        deliveries = queue.Queue()
        reader = DaemonReader(call=call, dispatch=lambda *args: deliveries.put(args))
        readers.append(reader)
        return reader, deliveries

    make.release_events = release_events
    yield make
    for reader in readers:
        reader.close()
    for event in release_events:
        event.set()
    for reader in readers:
        reader._worker.join(timeout=2)
        assert not reader._worker.is_alive()


def deliver(deliveries):
    callback, *args = deliveries.get(timeout=2)
    result = callback(*args)
    if result == GLib.SOURCE_CONTINUE:
        deliveries.put((callback, *args))
    return result


def test_slow_read_stays_on_worker_and_delivery_waits_for_ui(reader_factory):
    entered, release = threading.Event(), threading.Event()
    reader_factory.release_events.append(release)
    main_thread = threading.get_ident()
    called = []

    def call(method, params, reply_type, timeout):
        called.append((threading.get_ident(), method, params, reply_type.dup_string(), timeout))
        entered.set()
        assert release.wait(2)
        return reply({"id": 7})

    reader, deliveries = reader_factory(call)
    observed = []
    reader.request("task:7", "GetTask", (7,),
                   lambda value, error: observed.append((threading.get_ident(), value, error)))
    assert entered.wait(2)
    assert observed == [] and deliveries.empty()
    assert called == [(reader._worker.ident, "GetTask", (7,), "(s)", 3000)]
    assert called[0][0] != main_thread
    release.set()
    assert deliver(deliveries) == GLib.SOURCE_REMOVE
    assert observed == [(main_thread, {"id": 7}, None)]


def test_new_request_replaces_inflight_result_and_intermediate_pending_read(reader_factory):
    entered, release = threading.Event(), threading.Event()
    reader_factory.release_events.append(release)
    calls = []

    def call(_method, value, *_args, **_kwargs):
        calls.append(value)
        if value == "old":
            entered.set()
            assert release.wait(2)
        return reply(value)

    reader, deliveries = reader_factory(call)
    observed = []
    callback = lambda value, error: observed.append((value, error))
    reader.request("overview", "TaskOverview", "old", callback)
    assert entered.wait(2)
    reader.request("overview", "TaskOverview", "intermediate", callback)
    reader.request("overview", "TaskOverview", "latest", callback)
    release.set()
    deliver(deliveries)
    assert calls == ["old", "latest"]
    assert observed == [("latest", None)]
    assert deliveries.empty()


def test_superseding_after_dispatch_invalidates_an_already_queued_callback(reader_factory):
    release_new = threading.Event()
    reader_factory.release_events.append(release_new)

    def call(_method, value, *_a, **_k):
        if value == "new":
            assert release_new.wait(2)
        return reply(value)

    reader, deliveries = reader_factory(call)
    observed = []
    callback = lambda value, error: observed.append((value, error))
    reader.request("overview", "TaskOverview", "old", callback)
    old_delivery = deliveries.get(timeout=2)
    reader.request("overview", "TaskOverview", "new", callback)
    old_delivery[0](*old_delivery[1:])
    assert observed == []
    release_new.set()
    deliver(deliveries)
    assert observed == [("new", None)]


def test_close_invalidates_queued_delivery_and_rejects_new_requests(reader_factory):
    calls, observed = [], []

    def call(_method, value, *_a, **_k):
        calls.append(value)
        return reply(value)

    reader, deliveries = reader_factory(call)
    callback = lambda value, error: observed.append((value, error))
    reader.request("overview", "TaskOverview", "before_close", callback)
    queued = deliveries.get(timeout=2)
    reader.close()
    reader.request("overview", "TaskOverview", "after_close", callback)
    queued[0](*queued[1:])
    reader._worker.join(timeout=2)
    assert observed == []
    assert calls == ["before_close"]


def test_close_during_read_discards_payload_and_never_starts_pending_work(reader_factory):
    entered, release = threading.Event(), threading.Event()
    reader_factory.release_events.append(release)
    calls, observed = [], []

    def call(_method, value, *_a, **_k):
        calls.append(value)
        entered.set()
        assert release.wait(2)
        return reply(value)

    reader, deliveries = reader_factory(call)
    callback = lambda value, error: observed.append((value, error))
    reader.request("first", "GetTask", 1, callback)
    assert entered.wait(2)
    reader.request("pending", "GetTask", 2, callback)
    reader.close()
    release.set()
    reader._worker.join(timeout=2)
    assert not reader._worker.is_alive()
    assert calls == [1] and observed == [] and deliveries.empty()


def test_one_bounded_worker_drops_oldest_pending_keys(reader_factory):
    entered, release = threading.Event(), threading.Event()
    reader_factory.release_events.append(release)

    def call(*_a, **_k):
        entered.set()
        assert release.wait(2)
        return reply(None)

    reader, _deliveries = reader_factory(call)
    reader.request("inflight", "GetTask", 0, lambda *_: None)
    assert entered.wait(2)
    for key in range(150):
        reader.request(key, "GetTask", key, lambda *_: None)
    with reader._condition:
        assert list(reader._pending) == list(range(22, 150))
        assert len(reader._latest) == 129  # Pending bound plus the current read.
    reader.close()
    release.set()


@pytest.mark.parametrize("failure", [RuntimeError("Daemon unavailable"), "malformed JSON"])
def test_error_is_delivered_without_killing_subsequent_reads(reader_factory, failure):
    def call(_method, value, *_a, **_k):
        if value == "bad":
            if isinstance(failure, Exception):
                raise failure
            return GLib.Variant("(s)", (failure,))
        return reply(value)

    reader, deliveries = reader_factory(call)
    observed = []
    callback = lambda value, error: observed.append((value, error))
    reader.request("overview", "TaskOverview", "bad", callback)
    deliver(deliveries)
    assert observed[0][0] is None and isinstance(observed[0][1], Exception)
    reader.request("overview", "TaskOverview", "good", callback)
    deliver(deliveries)
    assert observed[1] == ("good", None)


def test_blocked_ui_keeps_one_drain_and_only_latest_128_completed_keys(reader_factory):
    calls = queue.Queue()
    release = threading.Event()
    reader_factory.release_events.append(release)

    def call(_method, value, *_a, **_k):
        calls.put(value)
        if value == "barrier":
            assert release.wait(2)
        return reply(value)

    reader, deliveries = reader_factory(call)
    observed = []
    callback = lambda value, error: observed.append((value, error))
    # The UI keeps issuing new reads but deliberately does not drain completions.
    for key in range(200):
        reader.request(key, "GetTask", key, callback)
        assert calls.get(timeout=2) == key
    reader.request("barrier", "GetTask", "barrier", callback)
    assert calls.get(timeout=2) == "barrier"  # All prior reads have completed.
    with reader._condition:
        assert list(reader._completed) == list(range(72, 200))
        assert len(reader._latest) == 129
    assert deliveries.qsize() == 1
    for _ in range(128):
        deliver(deliveries)
    assert observed == [(i, None) for i in range(72, 200)]
    assert deliveries.empty()
    reader.close()
    release.set()


def test_queued_completions_for_one_key_are_replaced_without_extra_idle_sources(reader_factory):
    calls = queue.Queue()
    release = threading.Event()
    reader_factory.release_events.append(release)

    def call(_method, value, *_a, **_k):
        calls.put(value)
        if value == "barrier":
            assert release.wait(2)
        return reply(value)

    reader, deliveries = reader_factory(call)
    observed = []
    callback = lambda value, error: observed.append((value, error))
    for version in range(20):
        reader.request("overview", "TaskOverview", version, callback)
        assert calls.get(timeout=2) == version
    reader.request("barrier", "GetTask", "barrier", callback)
    assert calls.get(timeout=2) == "barrier"
    assert deliveries.qsize() == 1
    deliver(deliveries)
    assert observed == [(19, None)]
    reader.close()
    release.set()


def test_failed_view_callback_releases_generation_and_allows_next_delivery(reader_factory, caplog):
    reader, deliveries = reader_factory(lambda _method, value, *_a, **_k: reply(value))

    def broken_view(_value, _error):
        raise RuntimeError("Fixture view failure")

    reader.request("overview", "TaskOverview", "old", broken_view)
    assert deliver(deliveries) == GLib.SOURCE_REMOVE
    assert "Could not display a daemon read result" in caplog.text
    observed = []
    reader.request("overview", "TaskOverview", "new", lambda value, error: observed.append((value, error)))
    deliver(deliveries)
    assert observed == [("new", None)]
    with reader._condition:
        assert not reader._latest and not reader._completed
