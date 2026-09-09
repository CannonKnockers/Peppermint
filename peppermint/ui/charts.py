"""Small Cairo resource charts. Missing samples remain visible gaps."""

from __future__ import annotations

import math

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


MINT = (0.66, 0.92, 0.77)
AMBER = (0.92, 0.76, 0.49)


def finite_number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def segments(points, series_index: int, gap_seconds: float):
    """Return only observed segments; nulls, explicit breaks and time gaps split them."""
    result, current = [], []
    previous_time = None
    for timestamp, values, break_before in points:
        value = finite_number(values[series_index]) if series_index < len(values) else None
        timestamp = finite_number(timestamp)
        if (timestamp is None or value is None or break_before
                or (previous_time is not None and (timestamp <= previous_time or timestamp - previous_time > gap_seconds))):
            if current:
                result.append(current)
            current = []
        if timestamp is not None and value is not None:
            current.append((timestamp, value))
        previous_time = timestamp
    if current:
        result.append(current)
    return result


class TimeSeriesChart(Gtk.DrawingArea):
    def __init__(self, unit: str, colors=(MINT,), ceiling: float | None = None):
        super().__init__()
        self.unit, self.colors, self.ceiling = unit, colors, ceiling
        self.points = []
        self.window_s, self.gap_seconds = 60, 5.0
        self.set_size_request(210, 104)
        self.set_hexpand(True)
        self.connect('draw', self._draw)
        self.get_accessible().set_name(f'{unit} resource history')

    def set_points(self, points, window_s=60, gap_seconds=5.0):
        self.points = list(points)[-150:]
        self.window_s, self.gap_seconds = window_s, gap_seconds
        self.queue_draw()

    def _draw(self, _widget, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        left, top, right, bottom = 4.0, 18.0, max(5.0, width - 4.0), max(20.0, height - 22.0)
        plot_w, plot_h = right - left, bottom - top
        cr.select_font_face('Sans')
        cr.set_font_size(10)
        cr.set_source_rgba(0.70, 0.81, 0.75, 0.15)
        cr.set_line_width(1)
        for fraction in (0, 0.5, 1):
            y = top + fraction * plot_h + 0.5
            cr.move_to(left, y)
            cr.line_to(right, y)
        cr.stroke()
        latest = self.points[-1][0] if self.points else 0.0
        earliest = latest - self.window_s
        visible = [point for point in self.points if earliest <= point[0] <= latest]
        values = [value for _, series, _ in visible for raw in series
                  if (value := finite_number(raw)) is not None]
        maximum = self.ceiling or max([1.0, *(max(0.0, value) * 1.12 for value in values)])
        maximum_text = f'{maximum:.0f}' if maximum >= 10 else f'{maximum:.1f}'
        cr.set_source_rgb(0.64, 0.75, 0.69)
        cr.move_to(left, 11)
        cr.show_text(f'{maximum_text} {self.unit}')
        for fraction, label in ((0, f'-{self.window_s}s'), (0.5, f'-{self.window_s // 2}s'), (1, 'latest')):
            extent = cr.text_extents(label)
            text_width = extent.width if hasattr(extent, 'width') else extent[2]
            cr.move_to(left + fraction * plot_w - fraction * text_width, height - 4)
            cr.show_text(label)
        if not values:
            label = 'Waiting for samples' if not visible else 'No measurement available'
            extent = cr.text_extents(label)
            text_width = extent.width if hasattr(extent, 'width') else extent[2]
            cr.move_to(max(left, (width - text_width) / 2), top + plot_h / 2 + 4)
            cr.show_text(label)
            return False
        cr.save()
        cr.rectangle(left, top - 2, plot_w, plot_h + 4)
        cr.clip()
        for index, color in enumerate(self.colors):
            for segment in segments(visible, index, self.gap_seconds):
                coordinates = [(left + (t - earliest) / self.window_s * plot_w,
                                bottom - min(max(v, 0), maximum) / maximum * plot_h)
                               for t, v in segment]
                cr.set_source_rgba(*color, 0.12)
                if len(coordinates) > 1:
                    cr.move_to(coordinates[0][0], bottom)
                    for x, y in coordinates:
                        cr.line_to(x, y)
                    cr.line_to(coordinates[-1][0], bottom)
                    cr.close_path()
                    cr.fill()
                    cr.move_to(*coordinates[0])
                    for x, y in coordinates[1:]:
                        cr.line_to(x, y)
                    cr.set_source_rgb(*color)
                    cr.set_line_width(1.8)
                    cr.stroke()
                else:
                    cr.set_source_rgb(*color)
                    cr.arc(*coordinates[0], 2.1, 0, math.tau)
                    cr.fill()
        cr.restore()
        return False
