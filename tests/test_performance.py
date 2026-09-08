"""Measured data must remain distinct from hypotheses and missing probes."""
import json
import pytest
from peppermint.daemon.tools import performance as p
from peppermint.daemon.tools.registry import Confirm, Context, ToolError, call


def test_memory_uses_available_not_free_and_converts_kib():
    data = p.parse_meminfo('MemTotal: 8000 kB\nMemFree: 100 kB\nMemAvailable: 2400 kB\ninvalid\nBad: N/A kB')
    assert data['MemAvailable'] == 2400 * 1024
    assert data['MemFree'] == 100 * 1024
    assert 'Bad' not in data


def test_cpu_avoids_double_counting_guests():
    before = p.parse_cpu_stat('cpu 10 0 20 100 10 0 0 0 9 0')
    after = p.parse_cpu_stat('cpu 30 0 30 150 30 0 0 0 29 0')
    assert p.cpu_rates(before, after) == {'utilization_pct': 30.0, 'io_wait_pct': 20.0}


def test_reset_or_missing_counters_are_unknown():
    assert p.counter_rate(5, 3, 1) is None
    assert p.counter_rate(None, 3, 1) is None
    assert p.counter_rate(5, 7, 2, 4096) == 4096


def test_gpu_unknown_decoder_is_not_zero():
    rows = p.parse_gpu_csv('RTX 3060 Ti, 8192, 7000, 1192, 10, [N/A]')
    assert rows[0]['decoder_pct'] is None
    assert rows[0]['free_mib'] == 1192


def test_swap_occupancy_does_not_become_thrashing():
    snapshot = {'memory': {'status': 'measured', 'total_bytes': 16*p.GIB,
                           'available_bytes': 8*p.GIB, 'swap_used_bytes': 2*p.GIB,
                           'swap_in_bytes_per_s': 0, 'swap_out_bytes_per_s': 0}}
    assessment = p.assess_multitasking(snapshot)
    assert any('occupancy alone is not thrashing' in f for f in assessment['findings'])
    assert {o['id'] for o in assessment['options']} == {'responsive', 'parallel', 'browser_ai'}
    assert 'waits rather than' in assessment['options'][0]['tradeoff']
    assert any('does not verify' in line for line in assessment['limitations'])


def test_low_headroom_options_do_not_claim_more_ram_adds_vram():
    snapshot = {'memory': {'total_bytes': 16*p.GIB, 'available_bytes': 0.3*p.GIB},
                'gpu': {'devices': [{'name': 'test', 'total_mib': 8192, 'free_mib': 200}]}}
    assessment = p.assess_multitasking(snapshot)
    assert assessment['options'][-1]['id'] == 'capacity'
    assert 'does not add GPU VRAM' in assessment['options'][-1]['steps'][1]
    assert 'browser tabs' in assessment['options'][2]['tradeoff'].lower()


def test_missing_probes_still_produce_valid_report(monkeypatch):
    monkeypatch.setattr(p, '_proc_sample', lambda: {})
    monkeypatch.setattr(p, '_gpu_snapshot', lambda: {'status': 'unavailable', 'devices': []})
    monkeypatch.setattr(p, '_ollama_snapshot', lambda: {'status': 'unavailable', 'models': []})
    monkeypatch.setattr(p, '_disk_snapshot', lambda: {'status': 'unavailable', 'filesystems': []})
    monkeypatch.setattr(p.time, 'sleep', lambda _: None)
    snapshot = json.loads(p.performance_snapshot())
    assert snapshot['memory']['available_bytes'] is None
    assert snapshot['cpu']['utilization_pct'] is None
    assert 'unknown' in p.render_assessment(snapshot)


@pytest.mark.parametrize('seconds', [True, 0, -1, 4, '1', float('inf'), float('nan')])
def test_sample_duration_is_bounded(seconds):
    with pytest.raises(ToolError):
        p.performance_snapshot(seconds)


def test_snapshot_requires_permission_before_any_probe(monkeypatch):
    monkeypatch.setattr(p, 'collect_snapshot', lambda *_: pytest.fail('Probe ran without permission'))
    assert isinstance(call('performance_snapshot', {}, Context(1, require_approval=True)), Confirm)
