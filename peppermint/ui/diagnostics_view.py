"""Opt-in GTK diagnostics with bounded history and no work on the GTK thread."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import threading

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

from peppermint.ui.charts import AMBER, MINT, TimeSeriesChart, finite_number


GIB = 1024 ** 3
MIB = 1024 ** 2


def label(text='', style='diagnostic-caption'):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_line_wrap(True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(70)
    widget.get_style_context().add_class(style)
    return widget


def number(value, suffix='', divisor=1.0, precision=1):
    value = finite_number(value)
    return '—' if value is None else f'{value / divisor:.{precision}f}{suffix}'


class MetricCard(Gtk.Box):
    def __init__(self, title, unit, legend, colors=(MINT,), ceiling=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_style_context().add_class('diagnostic-card')
        self.pack_start(label(title, 'section-title'), False, False, 0)
        self.value = label('—', 'diagnostic-value')
        if len(colors) > 1:
            self.value.get_style_context().add_class('diagnostic-value-rates')
        self.pack_start(self.value, False, False, 0)
        self.caption = label(legend)
        self.pack_start(self.caption, False, False, 0)
        if len(colors) > 1:
            legend_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            for index, text in enumerate(legend.split('/')):
                legend_row.pack_start(label('● ' + text.strip(), 'diagnostic-legend-first' if index == 0
                                            else 'diagnostic-legend-second'), False, False, 0)
            self.pack_start(legend_row, False, False, 0)
            self.caption.set_text('Rates between consecutive samples')
        self.chart = TimeSeriesChart(unit, colors, ceiling)
        self.pack_start(self.chart, True, True, 0)


class DiagnosticsView(Gtk.Box):
    """The host enables collection for its lifetime; Start and Pause control sampling."""

    def __init__(self, collector=None, monitor_factory=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.get_style_context().add_class('diagnostics')
        self.collector, self.monitor_factory = collector, monitor_factory
        self._monitor = None
        self._active = False
        self._wanted = False
        self._destroyed = False
        self._generation = 0
        self._pending_sources = set()
        self._queued_event = None
        self._retry_source = 0
        self._navigation_source = 0
        self._delivery_lock = threading.Lock()
        self._gap_pending = False
        self._interval = 2
        self._window_s = 60
        self.history = deque(maxlen=150)
        self._last_sample = None
        self._gpu_id = None
        self._selected_identity = None
        self._selected_name = ''
        self._updating_processes = False
        self._task = None
        self.on_track = None
        self.on_sample = None
        self.render_enabled = True
        self._load_css()
        self._build()
        self.connect('destroy', self._on_destroy)

    @staticmethod
    def _load_css():
        screen = Gdk.Screen.get_default()
        if screen:
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_name('diagnostics.css')))
            Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    @property
    def monitoring(self):
        return bool(self._wanted and self._active and self._monitor and self._monitor.running)

    def _build(self):
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading.pack_start(label('A closer look at your computer', 'diagnostic-heading'), True, True, 0)
        self.pack_start(heading, False, False, 0)
        self.status_label = label('Monitoring is off. Start when you want to inspect resource usage.')
        self.pack_start(self.status_label, False, False, 0)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls.get_style_context().add_class('diagnostic-toolbar')
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.start_button = Gtk.Button(label='Start monitoring')
        self.start_button.get_style_context().add_class('suggested-action')
        self.start_button.connect('clicked', lambda *_: self.pause_monitoring() if self._wanted else self.start_monitoring())
        row.pack_start(self.start_button, False, False, 0)
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        modes.get_style_context().add_class('linked')
        self.basic_button = Gtk.RadioButton.new_with_label_from_widget(None, 'Basic')
        self.advanced_button = Gtk.RadioButton.new_with_label_from_widget(self.basic_button, 'Advanced')
        for button in (self.basic_button, self.advanced_button):
            button.set_mode(False)
            modes.pack_start(button, False, False, 0)
        self.advanced_button.connect('toggled', self._mode_changed)
        row.pack_end(modes, False, False, 0)
        controls.pack_start(row, False, False, 0)
        options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.interval_selector = Gtk.ComboBoxText()
        for key, text in (('2', 'Every 2 seconds'), ('5', 'Every 5 seconds')):
            self.interval_selector.append(key, text)
        self.interval_selector.set_active_id('2')
        self.interval_selector.connect('changed', self._interval_changed)
        self.history_selector = Gtk.ComboBoxText()
        for key, text in (('60', 'Last 60 seconds'), ('300', 'Last 5 minutes')):
            self.history_selector.append(key, text)
        self.history_selector.set_active_id('60')
        self.history_selector.connect('changed', self._history_changed)
        options.pack_start(self.interval_selector, False, False, 0)
        options.pack_start(self.history_selector, False, False, 0)
        controls.pack_start(options, False, False, 0)
        self.pack_start(controls, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        self.scroller = scroll
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(160)
        self.pack_start(scroll, True, True, 0)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_end(10)
        scroll.add(content)
        self.cards = {}
        cards = Gtk.FlowBox()
        cards.set_selection_mode(Gtk.SelectionMode.NONE)
        cards.set_min_children_per_line(2)
        cards.set_max_children_per_line(3)
        cards.set_homogeneous(True)
        cards.set_column_spacing(10)
        cards.set_row_spacing(10)
        specs = [
            ('cpu', 'CPU', '%', 'Across all logical cores', (MINT,), 100),
            ('memory', 'MEMORY', 'GiB', 'RAM in use', (MINT,), None),
            ('gpu', 'GPU', '%', 'First available GPU · details below', (MINT,), 100),
            ('disk', 'DISK ACTIVITY', 'MiB/s', 'Read / write', (MINT, AMBER), None),
            ('network', 'NETWORK', 'MiB/s', 'Receive / send', (MINT, AMBER), None),
            ('swap', 'SWAP', 'GiB', 'Swap space in use', (MINT,), None),
        ]
        for key, title, unit, caption, colors, ceiling in specs:
            card = MetricCard(title, unit, caption, colors, ceiling)
            self.cards[key] = card
            cards.add(card)
        content.pack_start(cards, False, False, 0)
        self.history_note = label('No samples yet. Rates need two observations; missing readings leave gaps.')
        content.pack_start(self.history_note, False, False, 0)
        self.task_label = label('No task selected. Open Tasks → Diagnostics to compare recorded activity times.')
        self.task_label.get_style_context().add_class('diagnostic-surface')
        content.pack_start(self.task_label, False, False, 0)
        self.advanced_revealer = Gtk.Revealer()
        self.advanced_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.advanced_revealer.set_transition_duration(180)
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.advanced_revealer.add(advanced)
        content.pack_start(self.advanced_revealer, False, False, 0)
        cores_box = self._section('LOGICAL CORES')
        self.cores = Gtk.FlowBox()
        self.cores.set_selection_mode(Gtk.SelectionMode.NONE)
        self.cores.set_min_children_per_line(4)
        self.cores.set_max_children_per_line(12)
        self.cores.set_column_spacing(4)
        self.cores.set_row_spacing(4)
        cores_box.pack_start(self.cores, False, False, 0)
        self.core_note = label('Per-core readings appear after the second sample.')
        cores_box.pack_start(self.core_note, False, False, 0)
        advanced.pack_start(cores_box, False, False, 0)
        sensors = self._section('GRAPHICS, TEMPERATURE & PRESSURE')
        self.sensor_label = label('Monitoring has not started.')
        self.sensor_label.set_selectable(True)
        sensors.pack_start(self.sensor_label, False, False, 0)
        advanced.pack_start(sensors, False, False, 0)
        processes = self._section('PROCESSES')
        self.process_note = label('CPU 100% means one logical core. Multi-core work can exceed 100%.')
        processes.pack_start(self.process_note, False, False, 0)
        self.process_search = Gtk.SearchEntry()
        self.process_search.set_placeholder_text('Search sampled processes by name or PID')
        self.process_search.get_style_context().add_class('process-search')
        self.process_search.connect('search-changed', lambda *_: self.process_filter.refilter())
        processes.pack_start(self.process_search, False, False, 0)
        self._build_process_table(processes)
        self.process_details = label('Select a process to inspect its latest reported measurements.')
        self.process_details.set_selectable(True)
        processes.pack_start(self.process_details, False, False, 0)
        self.track_button = Gtk.Button(label="Begin tracking")
        self.track_button.set_sensitive(False)
        self.track_button.connect("clicked", self._begin_tracking)
        processes.pack_start(self.track_button, False, False, 0)
        advanced.pack_start(processes, False, False, 0)
        advanced.reorder_child(processes, 0)
        self.errors_label = label('')
        advanced.pack_start(self.errors_label, False, False, 0)

    @staticmethod
    def _section(title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class('diagnostic-surface')
        box.pack_start(label(title, 'section-title'), False, False, 0)
        return box

    def _build_process_table(self, parent):
        self.process_store = Gtk.ListStore(int, str, str, float, float, float, float, str, str, str, str, str)
        self.process_filter = self.process_store.filter_new()
        self.process_filter.set_visible_func(self._process_visible)
        self.process_sort = Gtk.TreeModelSort(model=self.process_filter)
        self.process_sort.set_sort_column_id(3, Gtk.SortType.DESCENDING)
        self.process_tree = Gtk.TreeView(model=self.process_sort)
        self.process_tree.set_headers_clickable(True)
        self.process_tree.set_enable_search(False)
        for title, display, sort_key, width in (
            ('PID', 0, 0, 52), ('Name', 1, 1, 135), ('CPU % · 1 core', 8, 3, 100),
            ('RSS MiB', 9, 4, 76), ('Read MiB/s', 10, 5, 86), ('Write MiB/s', 11, 6, 90),
        ):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            column = Gtk.TreeViewColumn(title, renderer, text=display)
            column.set_sort_column_id(sort_key)
            column.set_resizable(True)
            column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            column.set_fixed_width(width)
            column.set_expand(display == 1)
            self.process_tree.append_column(column)
        self.process_tree.get_selection().connect('changed', self._process_selected)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, 230)
        scroll.add(self.process_tree)
        parent.pack_start(scroll, False, False, 0)

    def _process_visible(self, model, iterator, _data=None):
        query = self.process_search.get_text().strip().casefold()
        return not query or query in model[iterator][1].casefold() or query in str(model[iterator][0])

    def set_active(self, active: bool):
        if self._destroyed or bool(active) == self._active:
            return
        self._active = bool(active)
        if self._active and self._wanted:
            self._begin()
        elif not self._active:
            self._stop_delivery()
            if self._wanted:
                self.status_label.set_text('Monitoring stopped while the workspace is inactive.')

    def start_monitoring(self):
        if self._destroyed:
            return
        self._wanted = True
        self.start_button.set_label('Pause monitoring')
        if self._active:
            self._begin()
        else:
            self.status_label.set_text('Monitoring will begin when the workspace is active.')

    def pause_monitoring(self):
        self._wanted = False
        self._stop_delivery()
        self.start_button.set_label('Start monitoring')
        self.status_label.set_text('Monitoring paused. The collected history stays visible.')

    def _begin(self):
        if not self._wanted or not self._active or self._destroyed:
            return False
        if self._monitor is None:
            factory = self.monitor_factory
            if factory is None:
                from peppermint.diagnostics import Monitor
                factory = Monitor
            self._monitor = factory(self._queue_sample, sampler=self.collector, interval=self._interval,
                                    max_samples=150, on_error=self._queue_error)
        self._monitor.set_interval(self._interval)
        if self._monitor.running or self._monitor.start():
            self.status_label.set_text('Monitoring · collecting local resource measurements…')
            return False
        self.status_label.set_text('Waiting for the previous sample to finish…')
        if not self._retry_source:
            self._retry_source = GLib.timeout_add(150, self._retry_start)
        return False

    def _retry_start(self):
        self._retry_source = 0
        self._begin()
        return GLib.SOURCE_REMOVE

    def _stop_delivery(self):
        with self._delivery_lock:
            self._generation += 1
            pending = tuple(self._pending_sources)
            self._pending_sources.clear()
            self._queued_event = None
        for source_id in pending:
            GLib.source_remove(source_id)
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0
        if self._navigation_source:
            GLib.source_remove(self._navigation_source)
            self._navigation_source = 0
        if self._monitor:
            self._monitor.stop(wait=False)
        self._gap_pending = bool(self.history)

    def _queue_sample(self, sample):
        self._queue_event('sample', sample)

    def _queue_error(self, error):
        self._queue_event('error', str(error)[:500])

    def _queue_event(self, kind, payload):
        # Called by Monitor's worker. The lock covers source creation so an
        # immediate GTK delivery cannot race registration or a visibility stop.
        with self._delivery_lock:
            if self._destroyed or not self._active or not self._wanted:
                return
            # A stalled main loop retains at most one current process inventory.
            self._queued_event = (kind, payload)
            if self._pending_sources:
                return
            generation = self._generation

            def deliver():
                with self._delivery_lock:
                    self._pending_sources.discard(source_id)
                    event = self._queued_event
                    self._queued_event = None
                    valid = generation == self._generation and not self._destroyed and self._wanted and self._active
                if valid and event:
                    if event[0] == 'sample':
                        self.ingest_sample(event[1])
                    else:
                        self.pause_monitoring()
                        self.status_label.set_text('Monitoring stopped: ' + event[1])
                return GLib.SOURCE_REMOVE

            source_id = GLib.idle_add(deliver)
            self._pending_sources.add(source_id)

    def _on_destroy(self, *_args):
        self._destroyed = True
        self._wanted = False
        self._stop_delivery()

    def _interval_changed(self, widget):
        self._interval = int(widget.get_active_id() or '2')
        if self._monitor:
            self._monitor.set_interval(self._interval)
        self._refresh_charts()

    def _history_changed(self, widget):
        self._window_s = int(widget.get_active_id() or '60')
        self._refresh_charts()
        if self._task:
            self.set_task_context(self._task)

    def _mode_changed(self, widget):
        self.advanced_revealer.set_reveal_child(widget.get_active())
        if self._navigation_source:
            GLib.source_remove(self._navigation_source)
            self._navigation_source = 0
        if widget.get_active():
            # Wait for the native revealer transition and one settled layout.
            self._navigation_source = GLib.timeout_add(220, self._scroll_to_mode)
        else:
            self.scroller.get_vadjustment().set_value(0)

    def _scroll_to_mode(self):
        self._navigation_source = 0
        if not self._destroyed and self.advanced_button.get_active():
            adjustment = self.scroller.get_vadjustment()
            adjustment.set_value(min(self.advanced_revealer.get_allocation().y,
                                     max(0, adjustment.get_upper() - adjustment.get_page_size())))
        return GLib.SOURCE_REMOVE

    def ingest_sample(self, sample):
        """Main-thread preview/test hook. It never starts a collector."""
        timestamp = finite_number(sample.get('monotonic')) if isinstance(sample, dict) else None
        if self._destroyed or timestamp is None or (self.history and timestamp <= self.history[-1]['monotonic']):
            return False
        elapsed_gap = bool(self.history and timestamp - self.history[-1]['monotonic'] > max(2.5 * self._interval, 5))
        sample = deepcopy(dict(sample, _gap_before=self._gap_pending or elapsed_gap))
        self._gap_pending = False
        # Keep historical metrics, not 150 copies of the full process inventory.
        self.history.append({key: sample[key] for key in (
            'monotonic', 'timestamp', 'cpu', 'memory', 'gpu', 'disk', 'network', '_gap_before') if key in sample})
        self._last_sample = sample
        devices = (sample.get('gpu') or {}).get('devices') or []
        if self._gpu_id is None and devices:
            self._gpu_id = devices[0].get('id')
            self.cards['gpu'].caption.set_text(str(devices[0].get('name', 'GPU'))[:80])
        if self.on_sample:
            self.on_sample(sample)
        if self.render_enabled:
            self._refresh_charts()
            self._refresh_advanced(sample)
            self._refresh_processes(sample)
        if self._task:
            self.set_task_context(self._task)
        stamp = sample.get('timestamp', '')
        try:
            stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%H:%M:%S')
        except (TypeError, ValueError):
            stamp = 'time unavailable'
        prefix = 'Monitoring' if self.monitoring else 'Sample history'
        self.status_label.set_text(f'{prefix} · latest sample {stamp} · {len(self.history)} readings retained')
        return True

    def _values(self, sample):
        cpu, memory = sample.get('cpu') or {}, sample.get('memory') or {}
        devices = (sample.get('gpu') or {}).get('devices') or []
        gpu = next((device for device in devices if device.get('id') == self._gpu_id), {})
        disk, network = sample.get('disk') or {}, sample.get('network') or {}

        def divided(value, divisor):
            value = finite_number(value)
            return None if value is None else value / divisor

        return {
            'cpu': (finite_number(cpu.get('percent')),),
            'memory': (divided(memory.get('used_bytes'), GIB),),
            'gpu': (finite_number(gpu.get('utilization_pct')),),
            'disk': (divided(disk.get('read_bytes_per_s'), MIB), divided(disk.get('write_bytes_per_s'), MIB)),
            'network': (divided(network.get('rx_bytes_per_s'), MIB), divided(network.get('tx_bytes_per_s'), MIB)),
            'swap': (divided(memory.get('swap_used_bytes'), GIB),),
        }

    def _refresh_charts(self):
        latest = self.history[-1] if self.history else {}
        current = self._values(latest)
        memory = latest.get('memory') or {}
        for key, card in self.cards.items():
            points = [(sample['monotonic'], self._values(sample)[key], sample.get('_gap_before', False)) for sample in self.history]
            # Ingest records gaps using the cadence at collection time. A new
            # sampling interval must not reinterpret already collected history.
            card.chart.set_points(points, self._window_s, gap_seconds=float('inf'))
            values = current[key]
            if key in ('cpu', 'gpu'):
                card.value.set_text(number(values[0], '%'))
            elif key in ('memory', 'swap'):
                total = memory.get('total_bytes' if key == 'memory' else 'swap_total_bytes')
                card.value.set_text(f'{number(values[0])} / {number(total, divisor=GIB)} GiB')
                total_value = finite_number(total)
                card.chart.ceiling = total_value / GIB if total_value and total_value > 0 else None
            else:
                card.value.set_text(f'{number(values[0])} / {number(values[1])} MiB/s')
        disk_scope = (latest.get('disk') or {}).get('scope')
        if disk_scope:
            self.cards['disk'].caption.set_text(str(disk_scope).replace('_', ' ')[:70])
        gpu = latest.get('gpu') or {}
        selected = next((device for device in gpu.get('devices') or [] if device.get('id') == self._gpu_id), None)
        if selected:
            self.cards['gpu'].caption.set_text(str(selected.get('name', 'GPU'))[:80])
        elif self._gpu_id is not None:
            self.cards['gpu'].caption.set_text('Selected GPU unavailable in this sample')
        elif gpu.get('status'):
            self.cards['gpu'].caption.set_text('GPU reading ' + str(gpu['status']).replace('_', ' '))
        if self.history:
            span = min(self._window_s, self.history[-1]['monotonic'] - self.history[0]['monotonic'])
            self.history_note.set_text(f'{span:.0f}s of sampled history in this window · — unavailable · gaps mean no measurement')

    def _refresh_advanced(self, sample):
        for child in self.cores.get_children():
            self.cores.remove(child)
        cores = (sample.get('cpu') or {}).get('cores') or []
        for core in cores[:128]:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.get_style_context().add_class('diagnostic-core')
            box.pack_start(label(f'CPU {core.get("id", "?")} · {number(core.get("percent"), "%", precision=0)}'), False, False, 0)
            bar = Gtk.LevelBar.new_for_interval(0, 100)
            bar.set_value(finite_number(core.get('percent')) or 0)
            box.pack_start(bar, False, False, 0)
            self.cores.add(box)
        self.cores.show_all()
        cpu = sample.get('cpu') or {}
        self.core_note.set_text(f'{cpu.get("logical_count", "—")} logical cores · rates unavailable until a second observation'
                                if any(core.get('percent') is None for core in cores) else
                                f'{len(cores)} logical core readings' + (' · first 128 shown' if len(cores) > 128 else ''))
        lines = []
        load = cpu.get('load_avg') or []
        lines.append('Load average · ' + ' / '.join(number(value, precision=2) for value in load[:3])
                     + ' (1 / 5 / 15 minutes)' if load else 'Load average · unavailable')
        memory = sample.get('memory') or {}
        lines.append(f'Swap in / out · {number(memory.get("swap_in_bytes_per_s"), divisor=MIB)} / '
                     f'{number(memory.get("swap_out_bytes_per_s"), divisor=MIB)} MiB/s')
        gpu = sample.get('gpu') or {}
        for device in (gpu.get('devices') or [])[:16]:
            lines.append(f'{device.get("name", "GPU")} · {number(device.get("utilization_pct"), "%")} load · '
                         f'{number(device.get("temperature_c"), "°C")} · '
                         f'{number(device.get("memory_used_bytes"), divisor=GIB)} / '
                         f'{number(device.get("memory_total_bytes"), divisor=GIB)} GiB VRAM')
        if not gpu.get('devices'):
            lines.append('GPU · ' + str(gpu.get('status', 'unavailable')).replace('_', ' '))
        for sensor in (sample.get('temperatures') or [])[:12]:
            lines.append(f'{sensor.get("label", "Temperature")} · {number(sensor.get("celsius"), "°C")}')
        pressure = sample.get('pressure') or {}
        for key, title in (('cpu', 'CPU'), ('memory', 'Memory'), ('io', 'I/O')):
            metrics = pressure.get(key) or {}
            lines.append(f'{title} pressure · some {number((metrics.get("some") or {}).get("avg10"), "%")} · '
                         f'full {number((metrics.get("full") or {}).get("avg10"), "%")} (10s average)')
        for device in ((sample.get('disk') or {}).get('devices') or [])[:16]:
            lines.append(f'Disk {device.get("name", "?")} · read / write '
                         f'{number(device.get("read_bytes_per_s"), divisor=MIB)} / '
                         f'{number(device.get("write_bytes_per_s"), divisor=MIB)} MiB/s · '
                         f'{number(device.get("busy_pct"), "%")} busy')
        for interface in ((sample.get('network') or {}).get('interfaces') or [])[:16]:
            lines.append(f'Network {interface.get("name", "?")} · receive / send '
                         f'{number(interface.get("rx_bytes_per_s"), divisor=MIB)} / '
                         f'{number(interface.get("tx_bytes_per_s"), divisor=MIB)} MiB/s')
        self.sensor_label.set_text('\n'.join(lines))
        errors = sample.get('errors') or []
        self.errors_label.set_text('\n'.join(str(error)[:240] for error in errors[:8]))

    def _refresh_processes(self, sample):
        processes = sample.get('processes') or {}
        items = processes.get('items') or []
        self._updating_processes = True
        self.process_store.clear()
        for process in items:
            pid, ticks = process.get('pid'), process.get('start_ticks')
            if not isinstance(pid, int) or not isinstance(ticks, int):
                continue
            raw = [finite_number(process.get(key)) for key in ('cpu_pct', 'rss_bytes', 'read_bytes_per_s', 'write_bytes_per_s')]
            self.process_store.append((pid, str(process.get('name', ''))[:200], str(ticks),
                *(value if value is not None else -1.0 for value in raw), str(process.get('state', '')),
                number(raw[0], precision=1), number(raw[1], divisor=MIB),
                number(raw[2], divisor=MIB), number(raw[3], divisor=MIB)))
        if self._selected_identity:
            for row in self.process_sort:
                if (row[0], int(row[2])) == self._selected_identity:
                    self.process_tree.get_selection().select_path(row.path)
                    break
        self._updating_processes = False
        self.process_note.set_text(f'Showing {len(items)} of {processes.get("total_count", "—")} processes · '
                                   + ('sampled subset · ' if processes.get('limited') else '')
                                   + 'CPU 100% = one logical core')
        self._refresh_selected_details()

    def _begin_tracking(self, *_):
        items = ((self._last_sample or {}).get("processes") or {}).get("items", [])
        process = next((p for p in items if (p.get("pid"), p.get("start_ticks")) == self._selected_identity), None)
        if process and self.on_track:
            self.on_track(process)

    def set_render_visible(self, visible):
        self.render_enabled = bool(visible)
        if visible and self._last_sample:
            self._refresh_charts()
            self._refresh_advanced(self._last_sample)
            self._refresh_processes(self._last_sample)

    def _process_selected(self, selection):
        if self._updating_processes:
            return
        model, iterator = selection.get_selected()
        if iterator is not None:
            row = model[iterator]
            self._selected_identity, self._selected_name = (row[0], int(row[2])), row[1]
            self._refresh_selected_details()

    def _refresh_selected_details(self):
        if not self._selected_identity:
            self.track_button.set_sensitive(False)
            return
        pid, ticks = self._selected_identity
        items = ((self._last_sample or {}).get('processes') or {}).get('items') or []
        process = next((item for item in items if (item.get('pid'), item.get('start_ticks')) == (pid, ticks)), None)
        self.track_button.set_sensitive(process is not None)
        title = f'{self._selected_name} · PID {pid} · start {ticks}'
        if process is None:
            message = ('PID has been reused by another process.' if any(item.get('pid') == pid for item in items)
                       else 'This process has exited or is not included in the latest sample.')
            self.process_details.set_text(title + '\n' + message + ' The selection stays bound to its original identity.')
            return
        self.process_details.set_text(title + f' · state {process.get("state", "—")}\n'
            f'CPU {number(process.get("cpu_pct"), "% of one core")} · RSS {number(process.get("rss_bytes"), " MiB", MIB)} · '
            f'read {number(process.get("read_bytes_per_s"), " MiB/s", MIB)} · '
            f'write {number(process.get("write_bytes_per_s"), " MiB/s", MIB)}')

    def set_task_context(self, task):
        self._task = {key: task.get(key) for key in ('id', 'idea', 'status', 'created_at', 'updated_at')} if task else None
        if not task:
            self.task_label.set_text('No task selected. Open Tasks → Diagnostics to compare recorded activity times.')
            return
        self._task['steps'] = [{key: step.get(key) for key in ('ts', 'tool', 'status')}
                               for step in (task.get('steps') or [])[-3:]]
        title = f'Task {task.get("id", "?")} · {str(task.get("idea", ""))[:180]} · {task.get("status", "")}'
        events = []
        for key, caption in (('created_at', 'Task created'), ('updated_at', 'Task last updated')):
            if task.get(key):
                events.append(f'{caption}: {task[key]}')
        for step in (task.get('steps') or [])[-3:]:
            events.append(f'Recorded {step.get("ts", "time unavailable")} · {step.get("tool", "activity")} · '
                          f'current status {step.get("status", "")} {self._event_window_label(step.get("ts"))}')
        self.task_label.set_text(title + ('\n' + '\n'.join(events) if events else '\nNo recorded activity for this task.')
                                 + '\nRecorded times describe step creation, not completion. Samples do not attribute resource usage to the task.')

    def _event_window_label(self, timestamp):
        def parsed(value):
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except (TypeError, ValueError, AttributeError):
                return None

        when = parsed(timestamp)
        samples = [(parsed(sample.get('timestamp')), sample.get('_gap_before', False)) for sample in self.history]
        samples = [(time, gap) for time, gap in samples if time is not None]
        if when is None or not samples:
            return '(no matching sample window)'
        if when < max(samples[0][0], samples[-1][0] - self._window_s) or when > samples[-1][0]:
            return '(outside sampled window)'
        for (before, _), (after, gap) in zip(samples, samples[1:]):
            if before < when < after and gap:
                return '(during a sampling gap)'
        return '(within sampled window)'
