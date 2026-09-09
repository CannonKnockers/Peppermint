"""Charts are instantiated only when a tracking report is opened."""
from gi.repository import Gtk
from peppermint.ui.charts import TimeSeriesChart


class TrackingReports(Gtk.Box):
    def __init__(self, tracking, on_stop):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.tracking, self.on_stop = tracking, on_stop
        self.report_id = None
        self.heading = Gtk.Label(xalign=0)
        self.pack_start(self.heading, False, False, 0)
        self.stop = Gtk.Button(label='Stop tracking and save report')
        self.stop.connect('clicked', lambda *_: on_stop(self.report_id))
        self.pack_start(self.stop, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.add(self.content)
        self.pack_start(scroll, True, True, 0)
        self.charts = {}

    def open_report(self, rid):
        self.report_id = rid
        if not self.charts:
            for key, title, unit, divisor in (
                ('cpu_pct', 'CPU · 100% = one logical core', '%', 1),
                ('rss_bytes', 'Resident memory', 'MiB', 1024**2),
                ('read_bytes_per_s', 'Disk reads', 'MiB/s', 1024**2),
                ('write_bytes_per_s', 'Disk writes', 'MiB/s', 1024**2)):
                self.content.pack_start(Gtk.Label(label=title, xalign=0), False, False, 0)
                chart = TimeSeriesChart(unit)
                self.content.pack_start(chart, False, False, 0)
                self.charts[key] = chart, divisor
        self.refresh()
        self.show_all()

    def refresh(self):
        if self.report_id not in self.tracking.reports:
            return
        report = self.tracking.reports[self.report_id]
        self.heading.set_text(f"{report['name']} · PID {report['pid']} · "
                              f"{'Tracking' if report['active'] else 'Saved'} · {report['samples']} samples\n"
                              'Latest 150 readings retained. Missing readings appear as gaps.')
        self.stop.set_sensitive(report['active'])
        points = list(report['points'])
        for key, (chart, divisor) in self.charts.items():
            chart.set_points([(p['monotonic'], (p[key] / divisor if p[key] is not None else None,), False)
                              for p in points], window_s=max(60, points[-1]['monotonic'] - points[0]['monotonic']) if points else 60,
                             gap_seconds=12.5)
