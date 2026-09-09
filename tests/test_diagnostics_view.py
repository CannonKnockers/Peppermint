"""GTK diagnostics tests use fixtures and a fake monitor, never local probes."""

import threading

import gi
import pytest

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from peppermint.ui.charts import segments
from peppermint.ui.diagnostics_view import DiagnosticsView, GIB, MIB


@pytest.fixture(autouse=True)
def display():
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')


def flush():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def sample(t=0, processes=None, missing=False):
    return {
        'schema_version': 1, 'monotonic': 1000 + t, 'timestamp': f'2026-09-08T12:{int(t) // 60:02}:{int(t) % 60:02}+00:00',
        'cpu': {'percent': None if missing else 25.0, 'cores': [{'id': 0, 'percent': 40.0}, {'id': 1, 'percent': 10.0}], 'logical_count': 2},
        'memory': {'used_bytes': 4 * GIB, 'total_bytes': 16 * GIB, 'swap_used_bytes': 0, 'swap_total_bytes': 2 * GIB},
        'gpu': {'status': 'ok', 'devices': [{'id': 'gpu0', 'name': 'Fixture GPU', 'utilization_pct': None if missing else 30,
                                           'temperature_c': 52, 'memory_used_bytes': GIB, 'memory_total_bytes': 8 * GIB}]},
        'disk': {'read_bytes_per_s': None if missing else 3 * MIB, 'write_bytes_per_s': MIB, 'scope': 'physical devices'},
        'network': {'rx_bytes_per_s': None if missing else MIB, 'tx_bytes_per_s': 0},
        'pressure': {'cpu': {'some': {'avg10': 2.5}}},
        'temperatures': [{'label': 'Fixture sensor', 'celsius': 48}],
        'processes': {'items': processes or [], 'total_count': len(processes or []), 'limited': False},
        'errors': [],
    }


def process(pid=12, start=900, name='fixture-player', cpu=12.0):
    return {'pid': pid, 'start_ticks': start, 'name': name, 'state': 'S', 'cpu_pct': cpu,
            'rss_bytes': 200 * MIB, 'read_bytes_per_s': MIB, 'write_bytes_per_s': None}


class FakeMonitor:
    def __init__(self, callback, sampler=None, interval=2, max_samples=150, on_error=None):
        self.callback, self.interval, self.max_samples = callback, interval, max_samples
        self.on_error = on_error
        self.running = False
        self.starts, self.stops = 0, 0
        self.refuse_start = False

    def set_interval(self, interval):
        self.interval = interval

    def start(self):
        self.starts += 1
        self.running = not self.refuse_start
        return self.running

    def stop(self, wait=False):
        assert not wait
        self.stops += 1
        self.running = False
        return True


def view():
    return DiagnosticsView(monitor_factory=FakeMonitor)


def test_initial_construction_visibility_and_fixture_ingest_never_start_monitor():
    widget = view()
    widget.set_active(True)
    widget.show_all()
    assert widget._monitor is None and not widget.monitoring
    assert widget.ingest_sample(sample(missing=True))
    assert widget._monitor is None and not widget.monitoring
    assert widget.cards['cpu'].value.get_text() == '—'
    assert widget.cards['network'].value.get_text() == '— / 0.0 MiB/s'
    assert widget.cards['memory'].value.get_text() == '4.0 / 16.0 GiB'
    widget.destroy()


def test_start_delivers_worker_samples_on_main_loop_and_pause_discards_queued_delivery():
    widget = view()
    widget.set_active(True)
    widget.start_monitoring()
    monitor = widget._monitor
    assert widget.monitoring and monitor.starts == 1
    worker = threading.Thread(target=monitor.callback, args=(sample(),))
    worker.start(); worker.join(timeout=2)
    assert not widget.history
    flush()
    assert len(widget.history) == 1
    monitor.callback(sample(2))
    assert widget._pending_sources
    widget.pause_monitoring()
    flush()
    assert len(widget.history) == 1
    assert not widget._pending_sources and not widget.monitoring
    monitor.callback(sample(4))
    flush()
    assert len(widget.history) == 1
    widget.destroy()


def test_inactive_host_retains_intent_and_marks_resume_gap():
    widget = view()
    widget.set_active(True)
    widget.start_monitoring()
    widget._monitor.callback(sample())
    flush()
    widget.set_active(False)
    assert widget._wanted and not widget.monitoring
    assert 'inactive' in widget.status_label.get_text()
    widget._monitor.callback(sample(2))
    flush()
    assert len(widget.history) == 1
    widget.set_active(True)
    assert widget.monitoring and widget._monitor.starts == 2
    widget._monitor.callback(sample(4))
    flush()
    assert widget.history[-1]['_gap_before'] is True
    widget.destroy()


def test_destroy_removes_pending_callbacks_and_cannot_restart():
    widget = view()
    widget.set_active(True)
    widget.start_monitoring()
    monitor = widget._monitor
    monitor.callback(sample())
    widget.destroy()
    flush()
    assert not widget.history and not widget._pending_sources
    assert monitor.stops == 1
    widget.start_monitoring()
    monitor.callback(sample(2))
    flush()
    assert not widget.history and monitor.starts == 1


def test_worker_events_coalesce_and_terminal_error_stops_with_visible_status():
    widget = view()
    widget.set_active(True)
    widget.start_monitoring()
    for index in range(200):
        widget._monitor.callback(sample(index * 2))
    assert len(widget._pending_sources) == 1
    flush()
    assert len(widget.history) == 1 and widget.history[-1]['monotonic'] == 1398
    widget._monitor.on_error('Fixture sampler failed')
    flush()
    assert not widget.monitoring and not widget._wanted
    assert widget.status_label.get_text() == 'Monitoring stopped: Fixture sampler failed'
    assert not widget._pending_sources
    widget.destroy()


def test_restart_retry_is_removed_on_hide_and_interval_changes_do_not_start_another_worker():
    widget = view()
    widget.set_active(True)
    widget.start_monitoring()
    monitor = widget._monitor
    widget.interval_selector.set_active_id('5')
    assert monitor.interval == 5 and monitor.starts == 1
    widget.set_active(False)
    monitor.refuse_start = True
    widget.set_active(True)
    assert widget._retry_source
    widget.set_active(False)
    assert not widget._retry_source
    widget.destroy()


def test_history_is_bounded_owns_its_data_and_omits_historical_process_inventories():
    widget = view()
    for index in range(170):
        fixture = sample(index * 2, [process()])
        widget.ingest_sample(fixture)
    fixture['cpu']['percent'] = 99
    fixture['processes']['items'][0]['name'] = 'changed input'
    assert len(widget.history) == 150
    assert widget.history[0]['monotonic'] == 1040
    assert widget.history[-1]['cpu']['percent'] == 25
    assert 'processes' not in widget.history[-1]
    assert widget._last_sample['processes']['items'][0]['name'] == 'fixture-player'
    widget.history_selector.set_active_id('300')
    assert widget.cards['cpu'].chart.window_s == 300
    assert len(widget.cards['cpu'].chart.points) == 150
    assert not widget.ingest_sample(sample(1))
    assert not widget.ingest_sample({'monotonic': float('nan')})
    widget.destroy()


def test_gpu_history_does_not_switch_devices_when_the_selected_gpu_is_missing():
    widget = view()
    widget.ingest_sample(sample())
    second = sample(2)
    second['gpu']['devices'][0].update(id='gpu1', name='Other GPU', utilization_pct=99)
    widget.ingest_sample(second)
    assert widget.cards['gpu'].value.get_text() == '—'
    assert widget.cards['gpu'].chart.points[-1][1] == (None,)
    assert 'Selected GPU unavailable' in widget.cards['gpu'].caption.get_text()
    widget.destroy()


def test_process_search_numeric_sort_and_pid_reuse_keep_selection_identity():
    widget = view()
    widget.ingest_sample(sample(processes=[process(12, 900, 'player', 2), process(30, 901, 'worker', 100)]))
    assert widget.process_sort[0][0] == 30
    widget.process_tree.get_selection().select_path('1')
    assert widget._selected_identity == (12, 900)
    assert 'player · PID 12' in widget.process_details.get_text()
    widget.ingest_sample(sample(2, [process(12, 999, 'replacement', 4), process(30, 901, 'worker', 101)]))
    assert widget._selected_identity == (12, 900)
    assert 'PID has been reused' in widget.process_details.get_text()
    widget.process_search.set_text('replacement')
    widget.process_filter.refilter()
    assert len(widget.process_sort) == 1 and widget.process_sort[0][0] == 12
    widget.process_tree.get_selection().select_path('0')
    assert widget._selected_identity == (12, 999)
    widget.ingest_sample(sample(4))
    assert 'exited or is not included' in widget.process_details.get_text()
    widget.destroy()


def test_advanced_mode_and_controls_fit_the_existing_minimum_window_width():
    window = Gtk.Window()
    widget = view()
    window.add(widget)
    window.set_default_size(612, 720)
    window.show_all()
    widget.advanced_button.set_active(True)
    assert widget._navigation_source
    widget.ingest_sample(sample(processes=[process()]))
    flush()
    assert window.get_preferred_width()[0] <= 612
    assert widget.advanced_revealer.get_reveal_child()
    assert 'Fixture GPU' in widget.sensor_label.get_text()
    assert 'full —' in widget.sensor_label.get_text()
    widget.basic_button.set_active(True)
    assert not widget._navigation_source
    assert widget.scroller.get_vadjustment().get_value() == 0
    window.destroy()


def test_chart_segments_preserve_missing_data_explicit_pauses_and_elapsed_gaps():
    points = [(0, (0,), False), (2, (2,), False), (4, (None,), False),
              (6, (5,), False), (8, (8,), True), (20, (9,), False)]
    assert segments(points, 0, 5) == [[(0.0, 0.0), (2.0, 2.0)], [(6.0, 5.0)], [(8.0, 8.0)], [(20.0, 9.0)]]


def test_cadence_changes_preserve_recorded_history_and_event_gap_classification():
    widget = view()
    widget.interval_selector.set_active_id('5')
    widget.ingest_sample(sample())
    widget.ingest_sample(sample(5.1))
    chart = widget.cards['cpu'].chart
    connected = [[(1000.0, 25.0), (1005.1, 25.0)]]
    event = '2026-09-08T12:00:03+00:00'
    assert segments(chart.points, 0, chart.gap_seconds) == connected
    assert widget._event_window_label(event) == '(within sampled window)'

    widget.interval_selector.set_active_id('2')
    assert segments(chart.points, 0, chart.gap_seconds) == connected
    assert widget._event_window_label(event) == '(within sampled window)'

    widget.ingest_sample(sample(12))
    widget.interval_selector.set_active_id('5')
    assert segments(chart.points, 0, chart.gap_seconds) == connected + [[(1012.0, 25.0)]]
    assert widget._event_window_label('2026-09-08T12:00:08+00:00') == '(during a sampling gap)'
    widget.destroy()


def test_task_context_is_labelled_as_timing_context_without_resource_attribution():
    widget = view()
    widget.set_task_context({'id': 8, 'idea': 'Inspect game', 'status': 'running', 'steps': [
        {'tool': 'steam_game_diagnostics', 'status': 'ok', 'ts': '2026-09-08T12:00:00+00:00'},
    ]})
    assert 'steam_game_diagnostics' in widget.task_label.get_text()
    assert 'do not attribute resource usage' in widget.task_label.get_text()
    assert 'Recorded 2026-09-08T12:00:00+00:00' in widget.task_label.get_text()
    widget.set_task_context(None)
    assert 'No task selected' in widget.task_label.get_text()
    widget.destroy()


def test_advanced_device_rates_and_recorded_events_preserve_sampling_gap_scope():
    widget = view()
    first = sample()
    first['cpu']['load_avg'] = [0.5, 0.6, 0.7]
    first['memory']['swap_in_bytes_per_s'] = MIB
    first['disk']['devices'] = [{'name': 'fixture0', 'read_bytes_per_s': 2 * MIB, 'write_bytes_per_s': MIB, 'busy_pct': 5}]
    first['network']['interfaces'] = [{'name': 'fixture-net', 'rx_bytes_per_s': MIB, 'tx_bytes_per_s': 0}]
    widget.ingest_sample(first)
    sensor_text = widget.sensor_label.get_text()
    assert 'Load average · 0.50 / 0.60 / 0.70' in sensor_text
    assert 'Swap in / out · 1.0 / — MiB/s' in sensor_text
    assert 'Disk fixture0' in sensor_text and '5.0% busy' in sensor_text
    assert 'Network fixture-net' in sensor_text
    widget.ingest_sample(sample(10))
    widget.interval_selector.set_active_id('5')
    assert widget.history[-1]['_gap_before']
    widget.set_task_context({'id': 2, 'idea': 'Fixture task', 'status': 'done', 'steps': [
        {'tool': 'read_file', 'status': 'ok', 'ts': '2026-09-08T11:59:59+00:00'},
        {'tool': 'run_shell', 'status': 'ok', 'ts': '2026-09-08T12:00:05+00:00'},
    ]})
    assert '(outside sampled window)' in widget.task_label.get_text()
    assert '(during a sampling gap)' in widget.task_label.get_text()
    assert 'step creation, not completion' in widget.task_label.get_text()
    widget.destroy()
