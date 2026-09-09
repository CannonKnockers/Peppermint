"""Bounded process histories fed by the existing diagnostics sampler."""
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

METRICS = ('cpu_pct', 'rss_bytes', 'read_bytes_per_s', 'write_bytes_per_s')


class ProcessTracking:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.reports = {}
        for path in sorted(self.directory.glob('*.json'), reverse=True)[:20]:
            try:
                data = json.loads(path.read_text())
                data['points'] = deque(data['points'][-150:], maxlen=150)
                data['active'] = False
                self.reports[data['id']] = data
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @property
    def identities(self):
        return tuple((r['pid'], r['start_ticks']) for r in self.reports.values() if r['active'])

    def begin(self, process):
        identity = (process['pid'], process['start_ticks'])
        for report in self.reports.values():
            if report['active'] and (report['pid'], report['start_ticks']) == identity:
                return report['id']
        if len(self.identities) >= 3:
            raise ValueError('Three processes are already being tracked. Stop one before adding another.')
        rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
        self.reports[rid] = dict(id=rid, name=process['name'], pid=identity[0], start_ticks=identity[1],
                                active=True, points=deque(maxlen=150), samples=0)
        return rid

    def ingest(self, sample):
        items = {(p['pid'], p['start_ticks']): p for p in sample.get('processes', {}).get('items', [])}
        for report in self.reports.values():
            if not report['active']:
                continue
            process = items.get((report['pid'], report['start_ticks']), {})
            report['points'].append(dict(monotonic=sample['monotonic'], timestamp=sample.get('timestamp'),
                                         **{key: process.get(key) for key in METRICS}))
            report['samples'] += 1

    def stop(self, rid):
        report = self.reports[rid]
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = dict(report, active=False, points=list(report['points']))
        destination = self.directory / (rid + '.json')
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.chmod(0o600)
        temporary.replace(destination)
        report['active'] = False
        saved = sorted(key for key, value in self.reports.items() if not value['active'])
        for key in saved[:-20]:
            del self.reports[key]

    def close(self):
        for rid, report in list(self.reports.items()):
            if report['active']:
                self.stop(rid)
