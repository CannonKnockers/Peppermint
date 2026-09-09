import threading

import pytest

from peppermint.diagnostics import Monitor


def test_monitor_stop_suppresses_late_sample_and_refuses_overlapping_restart():
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()

    class Sampler:
        resets = 0
        def reset(self): self.resets += 1
        def sample(self):
            entered.set()
            assert release.wait(2)
            return {'value': 1}

    sampler = Sampler()
    monitor = Monitor(lambda sample: delivered.set(), sampler, interval=0.1)
    assert monitor.start()
    assert not monitor.start()
    assert entered.wait(1)
    assert monitor.running
    assert not monitor.stop(wait=False)
    assert not monitor.running
    assert not monitor.start()
    assert not delivered.is_set()
    release.set()
    assert monitor.stop(wait=True, timeout=1)
    assert monitor.snapshot_history() == []
    assert monitor.start()
    assert delivered.wait(1)
    assert monitor.stop(wait=True, timeout=1)
    assert sampler.resets == 2


def test_monitor_bounded_history_copies_and_worker_callback_can_stop():
    complete = threading.Event()

    class Sampler:
        count = 0
        def sample(self):
            self.count += 1
            return {'nested': {'count': self.count}, 'processes': {'items': [{'pid': 42}]}}

    sampler = Sampler()
    monitor = None

    def callback(sample):
        assert sample['processes'] == {'items': [{'pid': 42}]}
        sample['nested']['count'] = -1
        if sampler.count == 4:
            monitor.stop(wait=True)
            complete.set()

    monitor = Monitor(callback, sampler, interval=0.1, max_samples=2)
    assert monitor.start()
    assert complete.wait(2)
    assert monitor.stop(wait=True, timeout=1)
    history = monitor.snapshot_history()
    assert [s['nested']['count'] for s in history] == [3, 4]
    assert all('processes' not in sample for sample in history)
    history[0]['nested']['count'] = 999
    assert monitor.snapshot_history()[0]['nested']['count'] == 3


def test_interval_change_wakes_existing_schedule():
    first, second = threading.Event(), threading.Event()

    class Sampler:
        count = 0
        def sample(self):
            self.count += 1
            return {'count': self.count}

    def callback(sample):
        (first if sample['count'] == 1 else second).set()

    monitor = Monitor(callback, Sampler(), interval=5)
    monitor.start()
    try:
        assert first.wait(1)
        monitor.set_interval(0.1)
        assert second.wait(1)
    finally:
        assert monitor.stop(wait=True, timeout=1)


@pytest.mark.parametrize('interval', [0, -1, 61, float('nan'), float('inf')])
def test_invalid_intervals_fail_before_start(interval):
    with pytest.raises(ValueError):
        Monitor(lambda sample: None, interval=interval)


def test_sampler_failure_stops_without_repeating_or_exposing_exception_text():
    reported = threading.Event()
    errors = []

    class Sampler:
        def sample(self):
            raise ValueError('private exception contents')

    def on_error(message):
        errors.append(message)
        reported.set()

    monitor = Monitor(lambda sample: pytest.fail('unexpected callback'), Sampler(), on_error=on_error)
    monitor.start()
    assert reported.wait(1)
    assert monitor.stop(wait=True, timeout=1)
    assert monitor.last_error == 'Sampling failed: ValueError'
    assert errors == ['Sampling failed: ValueError']


def test_stop_suppresses_late_error_callback():
    entered, release = threading.Event(), threading.Event()
    errors = []

    class Sampler:
        def sample(self):
            entered.set()
            assert release.wait(2)
            raise ValueError('private exception contents')

    monitor = Monitor(lambda sample: None, Sampler(), on_error=errors.append)
    monitor.start()
    assert entered.wait(1)
    assert not monitor.stop(wait=False)
    release.set()
    assert monitor.stop(wait=True, timeout=1)
    assert errors == []
    assert monitor.last_error == 'Sampling failed: ValueError'


def test_callback_failure_reports_once_even_if_error_callback_fails():
    reported = threading.Event()

    class Sampler:
        def sample(self):
            return {'cpu': {}}

    def on_sample(sample):
        raise RuntimeError('private sample callback contents')

    def on_error(message):
        assert message == 'Sample callback failed: RuntimeError'
        reported.set()
        raise ValueError('private error callback contents')

    monitor = Monitor(on_sample, Sampler(), on_error=on_error)
    monitor.start()
    assert reported.wait(1)
    assert monitor.stop(wait=True, timeout=1)
    assert monitor.last_error == 'Sample callback failed: RuntimeError'


def test_invalid_error_callback_fails_before_start():
    with pytest.raises(TypeError, match='on_error'):
        Monitor(lambda sample: None, on_error='invalid')
