"""Bounded Linux samples without command lines, environments or system changes.

Counter semantics: docs.kernel.org/filesystems/proc.html,
docs.kernel.org/admin-guide/iostats.html, docs.kernel.org/accounting/psi.html,
and docs.kernel.org/networking/statistics.html. Disk sectors are 512 bytes;
process CPU is expressed as a percentage of one logical CPU and may exceed 100.
"""

from __future__ import annotations

import csv
from contextlib import suppress
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import threading
import time


MAX_READ = 262144
MAX_ERRORS = 16
GPU_TIMEOUT = 1.5
SAFE_NAME = re.compile(r'[A-Za-z0-9_.!:+-]{1,80}\Z')


def number(value, *, negative=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and (negative or result >= 0) else None


def rate(before, after, elapsed, scale=1):
    if before is None or after is None or elapsed is None or elapsed <= 0 or after < before:
        return None
    return round((after - before) * scale / elapsed, 3)


def cpu_percent(before, after, elapsed):
    if not before or not after or elapsed is None or any(b < a for a, b in zip(before, after)):
        return None
    delta = [b - a for a, b in zip(before, after)]
    total = sum(delta)
    return round(100 * (total - delta[3] - delta[4]) / total, 2) if total > 0 else None


def clean(text, limit=100):
    return ''.join(c if c.isprintable() else ' ' for c in text).strip()[:limit]


def parse_cpu(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r'cpu\d*', fields[0]):
            continue
        try:
            values = tuple(int(v) for v in fields[1:9])
        except ValueError:
            continue
        if len(values) >= 4 and all(v >= 0 for v in values):
            result[fields[0]] = values + (0,) * (8 - len(values))
    return result


def parse_fields(text, *, memory=False):
    result = {}
    for line in text.splitlines():
        fields = line.replace(':', '').split()
        if len(fields) < 2 or (memory and (len(fields) != 3 or fields[2] != 'kB')):
            continue
        try:
            value = int(fields[1])
        except ValueError:
            continue
        if value >= 0:
            result[fields[0]] = value * (1024 if memory else 1)
    return result


def parse_pressure(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in ('some', 'full'):
            continue
        row = {}
        for field in fields[1:]:
            key, sep, value = field.partition('=')
            parsed = number(value)
            if sep and parsed is not None and key in ('avg10', 'avg60', 'avg300', 'total'):
                row[key] = int(parsed) if key == 'total' else parsed
        if row:
            result[fields[0]] = row
    return result


def parse_process_stat(text, page_size):
    """The comm field may contain spaces and parentheses; it is not a command."""
    left, right = text.find('('), text.rfind(')')
    if left < 1 or right <= left:
        return None
    fields = text[right + 1:].split()
    try:
        pid = int(text[:left].strip())
        ticks = int(fields[11]) + int(fields[12])
        start = int(fields[19])
        rss = int(fields[21])
        state = fields[0]
    except (IndexError, ValueError):
        return None
    if pid <= 0 or min(ticks, start) < 0 or len(state) != 1:
        return None
    return {'pid': pid, 'name': clean(text[left + 1:right]), 'start_ticks': start,
            'state': state, 'ticks': ticks, 'rss_bytes': rss * page_size if rss >= 0 else None}


class SamplingStopped(Exception):
    pass


class LinuxSampler:
    def __init__(self, proc_root='/proc', sys_root='/sys', *, clock=time.monotonic,
                 gpu_probe=None, max_processes=2048, max_process_items=256, process_budget_s=0.25):
        self.proc = Path(proc_root)
        self.sys = Path(sys_root)
        self.clock = clock
        self.gpu_probe = gpu_probe
        self.max_processes = max(1, min(int(max_processes), 4096))
        self.max_process_items = max(1, min(int(max_process_items), self.max_processes))
        self.process_budget_s = max(0.01, min(float(process_budget_s), 1.0))
        self.page_size = os.sysconf('SC_PAGE_SIZE')
        self.clock_ticks = os.sysconf('SC_CLK_TCK')
        self._previous = {}
        self._lock = threading.Lock()
        self._cancelled = lambda: False
        self._errors = []

    def reset(self):
        """A resumed monitor starts fresh rates rather than averaging its pause."""
        with self._lock:
            self._previous = {}

    def _check(self):
        if self._cancelled():
            raise SamplingStopped()

    def _error(self, label, reason='unavailable'):
        message = clean(f'{label}: {reason}', 160)
        if len(self._errors) < MAX_ERRORS and message not in self._errors:
            self._errors.append(message)

    def _read(self, path, limit=MAX_READ, *, quiet=False):
        self._check()
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('not a regular kernel file')
            data = os.read(fd, limit + 1)
            if len(data) > limit:
                raise OSError('read limit exceeded')
            return data.decode('utf-8', errors='replace')
        except OSError:
            if not quiet:
                self._error(path.name)
            return ''
        finally:
            if fd is not None:
                os.close(fd)

    def _exists(self, path):
        self._check()
        return path.exists()

    def _names(self, path, limit):
        self._check()
        names, limited = [], False
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    self._check()
                    if len(names) >= limit:
                        limited = True
                        break
                    names.append(entry.name)
        except OSError:
            self._error(path.name)
            return [], True
        return names, limited

    def _disk(self):
        result = {}
        for line in self._read(self.proc / 'diskstats').splitlines()[:1024]:
            self._check()
            fields = line.split()
            if len(fields) < 14 or not SAFE_NAME.fullmatch(fields[2]):
                continue
            name = fields[2]
            device = self.sys / 'class/block' / name
            if self._exists(device / 'partition') or not self._exists(device / 'device'):
                continue
            if name.startswith(('loop', 'ram', 'zram', 'dm-', 'md')):
                continue
            try:
                major, minor = int(fields[0]), int(fields[1])
                values = tuple(int(fields[i]) for i in (5, 9, 12))
            except ValueError:
                continue
            if min(values) < 0:
                continue
            if len(result) >= 64:
                self._error('disk', 'device limit reached')
                break
            result[name] = {'identity': (major, minor), 'read': values[0] * 512,
                            'write': values[1] * 512, 'busy_ms': values[2]}
        return result

    def _network(self):
        result = {}
        for line in self._read(self.proc / 'net/dev').splitlines()[:1024]:
            self._check()
            name, sep, rest = line.partition(':')
            name = name.strip()
            if not sep or name == 'lo' or not SAFE_NAME.fullmatch(name):
                continue
            device = self.sys / 'class/net' / name
            if not self._exists(device / 'device'):
                continue
            fields = rest.split()
            try:
                rx, tx = int(fields[0]), int(fields[8])
                index = int(self._read(device / 'ifindex', 128, quiet=True))
            except (ValueError, IndexError):
                continue
            if min(rx, tx, index) < 0:
                continue
            if len(result) >= 64:
                self._error('network', 'interface limit reached')
                break
            result[name] = {'identity': index, 'rx': rx, 'tx': tx}
        return result

    def _processes(self):
        names, limited = self._names(self.proc, 8192)
        pids = sorted(int(name) for name in names if name.isdecimal())
        total = None if limited else len(pids)
        result, scanned = {}, 0
        deadline = time.monotonic() + self.process_budget_s
        for pid in pids:
            self._check()
            if scanned >= self.max_processes or time.monotonic() > deadline:
                limited = True
                break
            scanned += 1
            path = self.proc / str(pid)
            before = parse_process_stat(self._read(path / 'stat', 8192, quiet=True), self.page_size)
            if not before or before['pid'] != pid:
                continue
            io = parse_fields(self._read(path / 'io', 4096, quiet=True))
            after = parse_process_stat(self._read(path / 'stat', 8192, quiet=True), self.page_size)
            if not after or (after['pid'], after['start_ticks']) != (pid, before['start_ticks']):
                continue
            after.update(read=io.get('read_bytes'), write=io.get('write_bytes'))
            result[(pid, after['start_ticks'])] = after
        return result, total, scanned, limited

    def _temperatures(self):
        rows = []
        names, _ = self._names(self.sys / 'class/thermal', 64)
        for name in sorted(names):
            if not re.fullmatch(r'thermal_zone\d+', name):
                continue
            path = self.sys / 'class/thermal' / name
            value = number(self._read(path / 'temp', 128, quiet=True), negative=True)
            if value is not None and -50000 <= value <= 200000:
                label = clean(self._read(path / 'type', 256, quiet=True), 64) or name
                rows.append({'label': label, 'celsius': value / 1000})
            if len(rows) >= 32:
                break
        return rows

    def _nvidia(self):
        executable = next((str(path) for path in (Path('/usr/bin/nvidia-smi'), Path('/bin/nvidia-smi'))
                           if self._exists(path)), None)
        if not executable:
            return 'unavailable', ''
        self._check()
        proc = subprocess.Popen([executable, '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                 '--format=csv,noheader,nounits'], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        chunks, size = [], 0
        deadline = time.monotonic() + GPU_TIMEOUT
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    self._check()
                    if time.monotonic() >= deadline:
                        return 'timeout', ''
                    if not selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                        continue
                    data = os.read(proc.stdout.fileno(), 4096)
                    if not data:
                        break
                    size += len(data)
                    if size > 16384:
                        return 'unavailable', ''
                    chunks.append(data)
            remaining = max(0.01, deadline - time.monotonic())
            code = proc.wait(timeout=remaining)
            return ('measured', b''.join(chunks).decode(errors='replace')) if code == 0 else ('unavailable', '')
        except subprocess.TimeoutExpired:
            return 'timeout', ''
        finally:
            try:
                if proc.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=0.5)
            finally:
                proc.stdout.close()

    def _gpu(self):
        self._check()
        try:
            status, text = self.gpu_probe(self._cancelled) if self.gpu_probe else self._nvidia()
        except (OSError, subprocess.SubprocessError):
            status, text = 'unavailable', ''
        devices = []
        for fields in csv.reader(text.splitlines()[:32]):
            if len(fields) != 6 or not fields[0].strip().isdigit() or not fields[1].strip():
                continue
            utilization, used, total, temperature = (number(v.strip()) for v in fields[2:])
            devices.append({'id': clean(fields[0], 32), 'name': clean(fields[1], 120),
                            'utilization_pct': utilization if utilization is None or utilization <= 100 else None,
                            'memory_used_bytes': int(used * 1048576) if used is not None else None,
                            'memory_total_bytes': int(total * 1048576) if total is not None else None,
                            'temperature_c': temperature if temperature is None or temperature <= 200 else None})
        if devices:
            status = 'measured' if all(all(value is not None for value in row.values()) for row in devices) else 'partial'
        else:
            status = status if status in ('timeout', 'unavailable') else 'unavailable'
            self._error('GPU', 'NVIDIA metrics unavailable' if status != 'timeout' else 'query timed out')
        return {'status': status, 'devices': devices}

    def sample(self, *, cancelled=None):
        """Read one sample; optional cancellation stops before subsequent probes."""
        with self._lock:
            self._cancelled = cancelled or (lambda: False)
            self._errors = []
            now = self.clock()
            timestamp = datetime.now(timezone.utc).isoformat()
            old = self._previous
            elapsed = now - old['monotonic'] if old and now > old['monotonic'] else None
            current = {'monotonic': now, 'cpu': parse_cpu(self._read(self.proc / 'stat')),
                       'memory': parse_fields(self._read(self.proc / 'meminfo'), memory=True),
                       'swap': parse_fields(self._read(self.proc / 'vmstat'))}
            pressure = {}
            for kind in ('cpu', 'memory', 'io'):
                row = parse_pressure(self._read(self.proc / 'pressure' / kind, 4096))
                pressure[kind] = row
                for label, values in row.items():
                    before = old.get('pressure', {}).get(kind, {}).get(label, {}).get('total')
                    percentage = rate(before, values.get('total'), elapsed, 0.0001)
                    values['sample_pct'] = min(100, percentage) if percentage is not None else None
            current['pressure'] = pressure
            current['disk'] = self._disk()
            current['network'] = self._network()
            current['processes'], count, scanned, limited = self._processes()
            load = [number(v) for v in self._read(self.proc / 'loadavg', 256).split()[:3]]
            cpu = current['cpu']
            memory = current['memory']
            total, available = memory.get('MemTotal'), memory.get('MemAvailable')
            swap_total, swap_free = memory.get('SwapTotal'), memory.get('SwapFree')
            items = []
            for identity, row in current['processes'].items():
                previous = old.get('processes', {}).get(identity, {})
                items.append({key: row[key] for key in ('pid', 'name', 'start_ticks', 'state', 'rss_bytes')} |
                             {'cpu_pct': rate(previous.get('ticks'), row['ticks'], elapsed, 100 / self.clock_ticks),
                              'read_bytes_per_s': rate(previous.get('read'), row['read'], elapsed),
                              'write_bytes_per_s': rate(previous.get('write'), row['write'], elapsed)})
            items.sort(key=lambda row: (row['cpu_pct'] if row['cpu_pct'] is not None else -1,
                                        row['rss_bytes'] if row['rss_bytes'] is not None else -1), reverse=True)
            limited = limited or len(items) > self.max_process_items
            disk_devices, interfaces = [], []
            for kind, rows, fields in (('disk', disk_devices, ('read', 'write')), ('network', interfaces, ('rx', 'tx'))):
                for name, values in current[kind].items():
                    before = old.get(kind, {}).get(name, {})
                    if before.get('identity') != values['identity']:
                        before = {}
                    row = {'name': name}
                    row.update({field + '_bytes_per_s': rate(before.get(field), values[field], elapsed) for field in fields})
                    if kind == 'disk':
                        busy = rate(before.get('busy_ms'), values['busy_ms'], elapsed, 0.1)
                        row['busy_pct'] = min(100, busy) if busy is not None else None
                    rows.append(row)
            def summed(rows, key, kind):
                if not rows or set(current[kind]) != set(old.get(kind, {})) or any(row[key] is None for row in rows):
                    return None
                return round(sum(row[key] for row in rows), 3)
            sample = {
                'schema_version': 1, 'timestamp': timestamp,
                'monotonic': now, 'elapsed_s': elapsed,
                'cpu': {'percent': cpu_percent(old.get('cpu', {}).get('cpu'), cpu.get('cpu'), elapsed),
                        'cores': [{'id': int(name[3:]), 'percent': cpu_percent(old.get('cpu', {}).get(name), values, elapsed)}
                                  for name, values in sorted(cpu.items(), key=lambda pair: int(pair[0][3:] or -1)) if name != 'cpu'],
                        'logical_count': sum(name != 'cpu' for name in cpu) or None,
                        'load_avg': load if len(load) == 3 and all(v is not None for v in load) else []},
                'memory': {'total_bytes': total, 'available_bytes': available,
                           'used_bytes': total - available if total is not None and available is not None and available <= total else None,
                           'swap_total_bytes': swap_total,
                           'swap_used_bytes': swap_total - swap_free if swap_total is not None and swap_free is not None and swap_free <= swap_total else None,
                           'swap_in_bytes_per_s': rate(old.get('swap', {}).get('pswpin'), current['swap'].get('pswpin'), elapsed, self.page_size),
                           'swap_out_bytes_per_s': rate(old.get('swap', {}).get('pswpout'), current['swap'].get('pswpout'), elapsed, self.page_size)},
                'disk': {'read_bytes_per_s': summed(disk_devices, 'read_bytes_per_s', 'disk'),
                         'write_bytes_per_s': summed(disk_devices, 'write_bytes_per_s', 'disk'), 'devices': disk_devices,
                         'scope': 'Physical whole block devices visible to this session; excludes partitions and virtual stacked devices. Not filesystem usage.'},
                'network': {'rx_bytes_per_s': summed(interfaces, 'rx_bytes_per_s', 'network'),
                            'tx_bytes_per_s': summed(interfaces, 'tx_bytes_per_s', 'network'), 'interfaces': interfaces,
                            'scope': 'Physical network interfaces only; excludes loopback and virtual interfaces to avoid duplicate accounting.'},
                'pressure': pressure, 'processes': {'items': items[:self.max_process_items],
                                                  'total_count': count, 'scanned': scanned, 'limited': limited},
                'temperatures': self._temperatures(), 'gpu': self._gpu(), 'errors': self._errors,
            }
            self._check()
            self._previous = current
            return sample
