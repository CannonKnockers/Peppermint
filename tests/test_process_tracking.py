import pytest
from peppermint.diagnostics.tracking import ProcessTracking


def process(pid=1, ticks=10):
    return dict(pid=pid, start_ticks=ticks, name='Example', cpu_pct=22, rss_bytes=1024,
                read_bytes_per_s=5, write_bytes_per_s=None)


def sample(t, *items):
    return dict(monotonic=t, timestamp=str(t), processes=dict(items=list(items)))


def test_limit_duplicate_identity_and_releasing_slot(tmp_path):
    tracker = ProcessTracking(tmp_path)
    first = tracker.begin(process())
    assert tracker.begin(process()) == first
    tracker.begin(process(2))
    tracker.begin(process(3))
    with pytest.raises(ValueError, match='Three'):
        tracker.begin(process(4))
    tracker.stop(first)
    tracker.begin(process(4))
    assert len(tracker.identities) == 3


def test_missing_samples_and_pid_reuse_never_switch_identity(tmp_path):
    tracker = ProcessTracking(tmp_path)
    rid = tracker.begin(process())
    tracker.ingest(sample(1, process()))
    tracker.ingest(sample(2, process(ticks=20)))
    points = tracker.reports[rid]['points']
    assert points[0]['cpu_pct'] == 22
    assert points[1]['cpu_pct'] is None


def test_history_is_bounded_and_report_survives_restart(tmp_path):
    tracker = ProcessTracking(tmp_path)
    rid = tracker.begin(process())
    for t in range(200):
        tracker.ingest(sample(t, process()))
    assert len(tracker.reports[rid]['points']) == 150
    assert not list(tmp_path.iterdir()), 'No disk write on each sample'
    tracker.close()
    restored = ProcessTracking(tmp_path)
    report = restored.reports[rid]
    assert not report['active']
    assert report['samples'] == 200
    assert len(report['points']) == 150
    assert report['points'][-1]['cpu_pct'] == 22
