import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from peppermint.diagnostics.sampler import LinuxSampler, SamplingStopped, parse_process_stat


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def process_stat(pid=123, ticks=100, start=7, rss=10, name='game (worker)'):
    fields = ['S'] + ['0'] * 21
    fields[11], fields[12], fields[19], fields[21] = str(ticks), '0', str(start), str(rss)
    return f'{pid} ({name}) ' + ' '.join(fields)


@pytest.fixture
def fixture(tmp_path):
    proc, kernel = tmp_path / 'proc', tmp_path / 'sys'
    write(proc / 'stat', 'cpu 100 0 50 850 0 0 0 0 99 99\ncpu0 100 0 50 850 0 0 0 0 99 99\n')
    write(proc / 'meminfo', 'MemTotal: 4096 kB\nMemAvailable: 1024 kB\nSwapTotal: 2048 kB\nSwapFree: 1024 kB\n')
    write(proc / 'vmstat', 'pswpin 10\npswpout 20\n')
    write(proc / 'loadavg', '0.5 0.25 0.1 1/100 123\n')
    for kind in ('cpu', 'memory', 'io'):
        write(proc / 'pressure' / kind, 'some avg10=1.0 avg60=0.5 avg300=0.1 total=100000\n')
    write(proc / 'diskstats', '8 0 sda 1 0 100 0 1 0 200 0 0 100 0\n'
          '8 1 sda1 1 0 100000 0 1 0 200000 0 0 10000 0\n'
          '253 0 dm-0 1 0 100000 0 1 0 200000 0 0 10000 0\n')
    for name in ('sda', 'sda1', 'dm-0'):
        (kernel / 'class/block' / name / 'device').mkdir(parents=True)
    write(kernel / 'class/block/sda1/partition', '1')
    write(proc / 'net/dev', 'eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n'
          'lo: 9000 0 0 0 0 0 0 0 9000 0 0 0 0 0 0 0\n'
          'veth0: 9000 0 0 0 0 0 0 0 9000 0 0 0 0 0 0 0\n')
    (kernel / 'class/net/eth0/device').mkdir(parents=True)
    write(kernel / 'class/net/eth0/ifindex', '2')
    write(kernel / 'class/thermal/thermal_zone0/temp', '42000')
    write(kernel / 'class/thermal/thermal_zone0/type', 'CPU package')
    write(proc / '123/stat', process_stat())
    write(proc / '123/io', 'read_bytes: 1000\nwrite_bytes: 2000\n')
    clock = [10.0]
    sampler = LinuxSampler(proc, kernel, clock=lambda: clock[0],
                           gpu_probe=lambda cancelled: ('measured', '0,Fixture GPU,12,100,1000,55\n'))
    sampler.clock_ticks = 100
    sampler.page_size = 4096
    return sampler, proc, kernel, clock


def test_initial_sample_contains_measurements_but_no_invented_rates(fixture):
    sampler, _, _, _ = fixture
    sample = sampler.sample()
    assert sample['schema_version'] == 1
    assert sample['timestamp'].endswith('+00:00')
    assert sample['monotonic'] == 10
    assert sample['elapsed_s'] is None
    assert sample['cpu']['percent'] is None
    assert sample['cpu']['cores'] == [{'id': 0, 'percent': None}]
    assert sample['memory']['used_bytes'] == 3 * 1024 * 1024
    assert sample['memory']['swap_in_bytes_per_s'] is None
    assert sample['processes']['items'][0]['cpu_pct'] is None
    assert sample['disk']['read_bytes_per_s'] is None
    assert sample['network']['rx_bytes_per_s'] is None
    assert sample['pressure']['cpu']['some']['sample_pct'] is None
    assert sample['gpu']['devices'][0]['memory_used_bytes'] == 100 * 1048576
    assert sample['temperatures'] == [{'label': 'CPU package', 'celsius': 42.0}]
    json.dumps(sample, allow_nan=False)


def test_counter_math_excludes_guest_ticks_partitions_and_virtual_networks(fixture):
    sampler, proc, _, clock = fixture
    sampler.sample()
    clock[0] = 12
    write(proc / 'stat', 'cpu 200 0 100 900 0 0 0 0 9999 9999\ncpu0 200 0 100 900 0 0 0 0\n')
    write(proc / 'vmstat', 'pswpin 14\npswpout 22\n')
    write(proc / 'diskstats', '8 0 sda 1 0 104 0 1 0 206 0 0 1100 0\n'
          '8 1 sda1 1 0 900000 0 1 0 900000 0 0 90000 0\n'
          '253 0 dm-0 1 0 900000 0 1 0 900000 0 0 90000 0\n')
    write(proc / 'net/dev', 'eth0: 3000 0 0 0 0 0 0 0 6000 0 0 0 0 0 0 0\n'
          'lo: 999999 0 0 0 0 0 0 0 999999 0 0 0 0 0 0 0\n')
    write(proc / '123/stat', process_stat(ticks=500))
    write(proc / '123/io', 'read_bytes: 3000\nwrite_bytes: 6000\n')
    write(proc / 'pressure/cpu', 'some avg10=1 avg60=0.5 avg300=0.1 total=1100000\n')
    sample = sampler.sample()
    assert sample['elapsed_s'] == 2
    assert sample['cpu']['percent'] == 75
    assert sample['cpu']['cores'][0]['percent'] == 75
    assert sample['memory']['swap_in_bytes_per_s'] == 8192
    assert sample['memory']['swap_out_bytes_per_s'] == 4096
    assert sample['disk']['devices'] == [{'name': 'sda', 'read_bytes_per_s': 1024,
                                          'write_bytes_per_s': 1536, 'busy_pct': 50}]
    assert sample['disk']['read_bytes_per_s'] == 1024
    assert sample['network']['interfaces'] == [{'name': 'eth0', 'rx_bytes_per_s': 1000, 'tx_bytes_per_s': 2000}]
    assert sample['processes']['items'][0]['cpu_pct'] == 200
    assert sample['processes']['items'][0]['read_bytes_per_s'] == 1000
    assert sample['pressure']['cpu']['some']['sample_pct'] == 50


def test_counter_reset_and_missing_fields_remain_unknown(fixture):
    sampler, proc, _, clock = fixture
    sampler.sample()
    clock[0] += 2
    write(proc / 'stat', 'cpu 1 0 1 1 0 0 0 0\n')
    write(proc / 'vmstat', 'pswpin 1\n')
    write(proc / 'meminfo', 'MemTotal: 4096 kB\n')
    write(proc / 'diskstats', '8 0 sda 1 0 1 0 1 0 1 0 0 1 0\n')
    write(proc / 'net/dev', 'eth0: 1 0 0 0 0 0 0 0 1 0 0 0 0 0 0 0\n')
    sample = sampler.sample()
    assert sample['cpu']['percent'] is None
    assert sample['memory']['used_bytes'] is None
    assert sample['memory']['swap_in_bytes_per_s'] is None
    assert sample['memory']['swap_out_bytes_per_s'] is None
    assert sample['disk']['read_bytes_per_s'] is None
    assert sample['network']['rx_bytes_per_s'] is None


def test_pid_reuse_and_intra_read_reuse_do_not_inherit_rates(fixture, monkeypatch):
    sampler, proc, _, clock = fixture
    sampler.sample()
    clock[0] += 2
    write(proc / '123/stat', process_stat(ticks=5000, start=9))
    sample = sampler.sample()
    assert sample['processes']['items'][0]['start_ticks'] == 9
    assert sample['processes']['items'][0]['cpu_pct'] is None
    assert sample['processes']['items'][0]['read_bytes_per_s'] is None
    real_read, calls = sampler._read, []

    def reused(path, *args, **kwargs):
        if path == proc / '123/stat':
            calls.append(path)
            return process_stat(start=10 if len(calls) == 1 else 11)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(sampler, '_read', reused)
    clock[0] += 2
    assert sampler.sample()['processes']['items'] == []


@pytest.mark.parametrize('clock_change', [0, -1])
def test_nonpositive_elapsed_and_explicit_reset_discard_baselines(fixture, clock_change):
    sampler, _, _, clock = fixture
    sampler.sample()
    clock[0] += clock_change
    sample = sampler.sample()
    assert sample['elapsed_s'] is None
    assert sample['cpu']['percent'] is None
    assert sample['processes']['items'][0]['cpu_pct'] is None
    sampler.reset()
    clock[0] += 100
    assert sampler.sample()['elapsed_s'] is None


def test_process_scan_and_return_limits_are_explicit(fixture):
    sampler, proc, _, _ = fixture
    for pid in range(200, 205):
        write(proc / str(pid) / 'stat', process_stat(pid=pid, rss=pid))
    sampler.max_processes = 2
    sampler.max_process_items = 1
    result = sampler.sample()['processes']
    assert result['total_count'] == 6
    assert result['scanned'] == 2
    assert result['limited'] is True
    assert len(result['items']) == 1


def test_sampler_never_reads_command_lines_or_environments(fixture, monkeypatch):
    sampler, proc, _, _ = fixture
    write(proc / '123/cmdline', 'secret arguments')
    write(proc / '123/environ', 'SECRET=value')
    real_read, paths = sampler._read, []

    def observed(path, *args, **kwargs):
        paths.append(path.name)
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(sampler, '_read', observed)
    sample = sampler.sample()
    assert 'cmdline' not in paths and 'environ' not in paths
    assert 'secret' not in json.dumps(sample)


def test_fifo_oversized_and_symlink_kernel_files_fail_without_blocking(fixture):
    sampler, proc, _, _ = fixture
    (proc / 'meminfo').unlink()
    os.mkfifo(proc / 'meminfo')
    write(proc / 'vmstat', 'x' * 262145)
    (proc / 'loadavg').unlink()
    (proc / 'loadavg').symlink_to(proc / 'vmstat')
    started = time.monotonic()
    sample = sampler.sample()
    assert time.monotonic() - started < 0.5
    assert sample['memory']['total_bytes'] is None
    assert sample['memory']['swap_in_bytes_per_s'] is None
    assert sample['cpu']['load_avg'] == []
    assert len(sample['errors']) <= 16
    assert all(len(error) <= 160 for error in sample['errors'])


def test_cancellation_stops_before_next_probe(fixture, monkeypatch):
    sampler, _, _, _ = fixture
    real_read, paths = sampler._read, []
    cancelled = [False]

    def observed(path, *args, **kwargs):
        result = real_read(path, *args, **kwargs)
        paths.append(path)
        cancelled[0] = True
        return result

    monkeypatch.setattr(sampler, '_read', observed)
    with pytest.raises(SamplingStopped):
        sampler.sample(cancelled=lambda: cancelled[0])
    assert len(paths) == 1
    assert not sampler._previous


def test_unavailable_gpu_fields_are_not_zero(fixture):
    sampler, _, _, _ = fixture
    sampler.gpu_probe = lambda cancelled: ('measured', '0,Fixture GPU,N/A,N/A,1000,N/A\n')
    sample = sampler.sample()
    assert sample['gpu']['status'] == 'partial'
    assert sample['gpu']['devices'][0]['utilization_pct'] is None
    assert sample['gpu']['devices'][0]['memory_used_bytes'] is None


@pytest.mark.parametrize('code, expected', [('import time; time.sleep(5)', 'timeout'),
                                           ('import os; os.write(1, b"x"*65536)', 'unavailable')])
def test_gpu_subprocess_is_time_and_output_bounded(monkeypatch, code, expected):
    import peppermint.diagnostics.sampler as module
    sampler = LinuxSampler()
    monkeypatch.setattr(sampler, '_exists', lambda path: True)
    monkeypatch.setattr(module, 'GPU_TIMEOUT', 0.1)
    popen, children = subprocess.Popen, []

    def launch(argv, **kwargs):
        assert '--query-gpu=' in argv[1]
        child = popen([sys.executable, '-c', code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    started = time.monotonic()
    assert sampler._nvidia()[0] == expected
    assert time.monotonic() - started < 1
    assert all(child.poll() is not None for child in children)


def test_process_stat_parentheses_and_invalid_stat():
    row = parse_process_stat(process_stat(name='a ) b ( c'), 4096)
    assert row['name'] == 'a ) b ( c'
    assert row['rss_bytes'] == 40960
    assert parse_process_stat('123 broken', 4096) is None
