"""Audit archive portability using temporary SQLite databases and synthetic media.

Run from the repository root:
    PYTHONPATH=. .venv/bin/python scripts/check_archive_roundtrip.py

Exits 1 if any check fails. No live tasks, timers or user files are modified.
"""
import json
import runpy
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon import archive
from peppermint.daemon.db import Database

fixtures = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tests/test_export_import.py'))
sample = fixtures['_build_sample_task']
FakeExportDb = fixtures['FakeExportDb']
results = []


def check(name, operation):
    try:
        operation()
    except Exception as exc:
        results.append((name, 'FAIL', f'{type(exc).__name__}: {exc}'))
    else:
        results.append((name, 'PASS', ''))


with tempfile.TemporaryDirectory(prefix='peppermint-roundtrip-') as folder:
    root = Path(folder)
    with patch.object(config, 'DATA_DIR', root / 'data'):
        def full_real_roundtrip():
            source = Database(root / 'source.db')
            source_id = source.add_task('Inspect a synthetic file')
            step = source.add_step(source_id, 'read_file', {'path': '/synthetic/file'}, 'safe', 'sample result')
            source.add_message(source_id, 'user', {'role': 'user', 'content': 'Inspect a synthetic file'})
            source.add_message(source_id, 'assistant', {'role': 'assistant', 'content': 'sample result'})
            source.add_confirmation(source_id, step, 'Read sample')
            source.set_status(source_id, Status.DONE)
            target = archive.export_archive(source, source_id, model='test-model', base_dir=root / 'exports')
            dest = Database(root / 'dest.db')
            dest.add_task('Existing unrelated task')
            result = archive.import_archive(dest, target, model='test-model')
            imported = dest.get_task(result['task_ids'][0])
            assert imported.id != source_id
            assert imported.steps[0].args == {'path': '/synthetic/file'}
            assert imported.messages == source.get_task(source_id).messages
        check('Real SQLite task with steps: export/import', full_real_roundtrip)

        def simple_roundtrip():
            source = Database(':memory:')
            task_id = source.add_task('Synthetic conversation without steps')
            source.add_message(task_id, 'user', {'role': 'user', 'content': 'Hello'})
            source.set_status(task_id, Status.DONE, result='Hello back')
            target = archive.export_archive(source, task_id, model='test-model', base_dir=root / 'simple')
            dest = Database(':memory:')
            dest.add_task('Existing task')
            result = archive.import_archive(dest, target, model='test-model')
            imported = dest.get_task(result['task_ids'][0])
            assert imported.id != task_id
            assert imported.messages == source.get_task(task_id).messages
            assert imported.result == 'Hello back'
        check('Real SQLite task without steps: new IDs and conversation', simple_roundtrip)

        def history_import():
            payload = sample(77)
            dest = Database(':memory:')
            dest.import_task_from_portable(sample(10))
            target = archive.export_archive(FakeExportDb([payload]), 77, model='test-model', base_dir=root / 'history')
            result = archive.import_archive(dest, target, model='test-model')
            task_id = result['task_ids'][0]
            step = dest.get_steps(task_id)[0]
            assert task_id == 2 and step.id != payload['steps'][0]['id']
            assert step.args == payload['steps'][0]['args']
            conn = dest.connection()
            for table, original in [('messages', payload['conversations'][0]), ('confirmations', payload['approvals'][0]), ('undo', payload['undo'][0])]:
                row = dict(conn.execute(f'SELECT * FROM {table} WHERE task_id = ?', (task_id,)).fetchone())
                for field, value in original.items():
                    if field == 'id':
                        assert row[field] != value
                    elif field == 'task_id':
                        assert row[field] == task_id
                    elif field == 'step_id':
                        assert row[field] == step.id
                    else:
                        assert row[field] == value, (table, field)
            assert dest.get_plan(task_id)[0]['evidence_step_id'] == step.id
        check('Valid archive import: conversations, approvals, undo and step links', history_import)

        def media_transfer():
            source_media = root / 'source-media'
            source_media.mkdir()
            screenshot = source_media / 'screen.png'
            download = source_media / 'download.txt'
            screenshot.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-image')
            download.write_bytes(b'synthetic download\n')
            payload = sample(77)
            payload['steps'][0]['args'] = {'screenshot': str(screenshot), 'download': str(download)}
            target = archive.export_archive(FakeExportDb([payload]), 77, model='test-model', base_dir=root / 'media-exports')
            with zipfile.ZipFile(target) as zf:
                contents = [zf.read(name) for name in zf.namelist() if name.startswith('media/')]
                assert screenshot.read_bytes() in contents and download.read_bytes() in contents
            screenshot.unlink()
            download.unlink()
            dest = Database(':memory:')
            result = archive.import_archive(dest, target, model='test-model')
            args = dest.get_steps(result['task_ids'][0])[0].args
            assert Path(args['screenshot']).is_file(), 'Screenshot not restored; imported history still points at the removed original path'
            assert Path(args['download']).read_bytes() == b'synthetic download\n'
        check('Media transfer after original files are removed', media_transfer)

        def fork_links():
            source = Database(':memory:')
            parent = source.add_task('Parent')
            child = source.add_task('Child')
            with source.connection() as conn:
                conn.execute('UPDATE tasks SET parent_task_id = ? WHERE id = ?', (parent, child))
            target = archive.export_archive(source, None, model='test-model', base_dir=root / 'graph')
            dest = Database(':memory:')
            for _ in range(5):
                dest.add_task('Unrelated existing task')
            result = archive.import_archive(dest, target, model='test-model')
            new_parent, new_child = result['task_ids']
            actual_parent = dest.get_task(new_child).parent_task_id
            assert actual_parent == new_parent, f'Imported child {new_child} points to old ID {actual_parent}, expected imported parent {new_parent}'
        check('Export-all/import: fork parent ID remapping', fork_links)

        def atomic_import():
            source = Database(':memory:')
            source.add_task('First')
            source.add_task('Second')
            target = archive.export_archive(source, None, model='test-model', base_dir=root / 'atomic')
            dest = Database(':memory:')
            original = dest.import_task_from_portable
            calls = 0
            def fail_second(task):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError('Synthetic database write failure')
                return original(task)
            with patch.object(dest, 'import_task_from_portable', fail_second):
                try:
                    archive.import_archive(dest, target, model='test-model')
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Expected synthetic failure')
            count = dest.connection().execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
            assert count == 0, f'{count} task remains from an import reported as failed'
        check('Import-all rollback after a later database failure', atomic_import)

        def versions():
            with patch.object(archive, '_peppermint_version', lambda: '0.2.3'):
                for version in ('0.2.0', '0.2.9', '0.1.9'):
                    archive._check_compatibility({'archive_format': 1, 'version': version})
                for version in ('0.3.0', '1.0.0', 'nonsense'):
                    try:
                        archive._check_compatibility({'archive_format': 1, 'version': version})
                    except archive.ImportError:
                        pass
                    else:
                        raise AssertionError(f'{version} should be rejected')
        check('Version compatibility after the four fixes', versions)

        def tilde_media():
            home = root / 'fake-home'
            home.mkdir()
            picture = home / 'screen.png'
            picture.write_bytes(b'synthetic-image')
            payload = sample(77)
            payload['steps'][0]['args']['path'] = '~/screen.png'
            with patch.object(Path, 'expanduser', lambda path: picture if str(path) == '~/screen.png' else path):
                target = archive.export_archive(FakeExportDb([payload]), 77, model='test-model', base_dir=root / 'tilde')
            with zipfile.ZipFile(target) as zf:
                assert any(zf.read(name) == b'synthetic-image' for name in zf.namelist() if name.startswith('media/'))
        check('Tilde media paths included on export', tilde_media)

for name, status, detail in results:
    print(f'{status}: {name}' + (f'\n  {detail}' if detail else ''))
raise SystemExit(any(status == 'FAIL' for _, status, _ in results))
