"""Real SQLite and ZIP round trips; no live database or user media is used."""
import json
import stat
import zipfile
from pathlib import Path

import pytest

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon import archive
from peppermint.daemon.db import Database


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    return Database(tmp_path / 'source.db'), Database(tmp_path / 'destination.db'), tmp_path


def create_task(db, media=None):
    task_id = db.add_task('Inspect synthetic files')
    step_id = db.add_step(task_id, 'read_file', {'path': str(media) if media else '/synthetic/input'}, 'safe', 'sample output')
    db.add_message(task_id, 'user', {'role': 'user', 'content': 'Inspect synthetic files'})
    db.add_message(task_id, 'assistant', {'role': 'assistant', 'content': json.dumps({'file': str(media)}) if media else 'sample reply'})
    db.add_confirmation(task_id, step_id, 'Read synthetic input')
    db.set_plan(task_id, [{'evidence_step_id': step_id, 'description': 'Check file'}])
    db.set_status(task_id, Status.DONE, result='Original result')
    return task_id


def export(db, root, task_id=None):
    return archive.export_archive(db, task_id, model='test-model', base_dir=root / 'exports')


def write_zip(path, tasks, media=None, manifest=None):
    metadata = dict(version='0.1.0', archive_format=1, model='test-model')
    if manifest is not None:
        metadata['media'] = manifest
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('tasks.json', json.dumps(tasks))
        zf.writestr('metadata.json', json.dumps(metadata))
        for name, content in (media or {}).items():
            zf.writestr(name, content)
    return path


def test_real_history_roundtrip_with_reexport(storage):
    source, dest, root = storage
    task_id = create_task(source)
    original = source.get_task(task_id)
    create_task(dest)  # Occupy both task and step IDs.
    path = export(source, root, task_id)
    for _ in range(2):
        result = archive.import_archive(dest, path, model='test-model')
        restored = dest.get_task(result['task_ids'][0])
        assert restored.id != original.id
        assert restored.result == original.result
        assert restored.messages == original.messages
        assert restored.steps[0].args == original.steps[0].args
        assert restored.steps[0].id != original.steps[0].id
        assert restored.pending.step_id == restored.steps[0].id
        assert restored.plan[0]['evidence_step_id'] == restored.steps[0].id
        assert restored.created_at == original.created_at
        assert restored.pending.description == original.pending.description
        path = export(dest, root, restored.id)
    assert dest.connection().execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 3


def test_media_relocated_in_steps_and_serialized_messages(storage):
    source, dest, root = storage
    media = root / 'screen.png'
    content = b'\x89PNG\r\n\x1a\nsynthetic screenshot'
    media.write_bytes(content)
    task_id = create_task(source, media)
    download = root / 'download.bin'
    download.write_bytes(b'download bytes')
    source.add_step(task_id, 'download_file', {'path': str(download)}, 'safe', json.dumps({'file': str(media)}))
    path = export(source, root)
    media.unlink()
    download.unlink()
    imported = []
    for _ in range(2):
        result = archive.import_archive(dest, path, model='test-model')
        task = dest.get_task(result['task_ids'][0])
        restored = Path(task.steps[0].args['path'])
        assert restored.read_bytes() == content
        assert restored.is_relative_to(config.DATA_DIR / 'imports')
        assert stat.S_IMODE(restored.stat().st_mode) == 0o600
        assert json.loads(task.messages[1]['content'])['file'] == str(restored)
        assert json.loads(task.steps[1].output)['file'] == str(restored)
        assert Path(task.steps[1].args['path']).read_bytes() == b'download bytes'
        imported.append(restored)
    assert imported[0] != imported[1]
    assert not media.exists() and not download.exists()
    # The restored media is portable again.
    again = export(dest, root, task.id)
    with zipfile.ZipFile(again) as zf:
        assert len(json.loads(zf.read('metadata.json'))['media']) == 2


def test_fork_parent_mapping_and_detached_single_export(storage):
    source, dest, root = storage
    parent = create_task(source)
    child = source.fork_task(parent, 0, 'Different idea')
    create_task(dest)
    path = export(source, root)
    result = archive.import_archive(dest, path, model='test-model')
    new_parent, new_child = result['task_ids']
    assert dest.get_task(new_child).parent_task_id == new_parent
    path = export(source, root, child)
    result = archive.import_archive(dest, path, model='test-model')
    assert dest.get_task(result['task_ids'][0]).parent_task_id == 0
    assert any('detached' in warning for warning in result['warnings'])


def test_cycle_rejected_before_any_insertion(storage):
    source, dest, root = storage
    first, second = create_task(source), create_task(source)
    tasks = source.export_tasks_with_history()
    tasks[0]['parent_task_id'], tasks[1]['parent_task_id'] = second, first
    path = write_zip(root / 'cycle.peppermint', tasks)
    with pytest.raises(ValueError, match='cycle'):
        archive.import_archive(dest, path, model='test-model')
    assert dest.list_tasks() == []


def test_database_failure_rolls_back_all_tasks_and_media(storage, monkeypatch):
    source, dest, root = storage
    media = root / 'image.png'
    media.write_bytes(b'sample')
    create_task(source, media)
    create_task(source)
    path = export(source, root)
    original = dest._insert_portable_task
    calls = 0
    def fail(conn, task):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Synthetic database failure')
        return original(conn, task)
    monkeypatch.setattr(dest, '_insert_portable_task', fail)
    with pytest.raises(RuntimeError, match='Synthetic'):
        archive.import_archive(dest, path, model='test-model')
    for table in ('tasks', 'steps', 'messages', 'confirmations', 'task_plans'):
        assert dest.connection().execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert list((config.DATA_DIR / 'imports').iterdir()) == []


def test_bulk_import_respects_callers_transaction(storage):
    source, dest, root = storage
    create_task(source)
    conn = dest.connection()
    conn.execute('BEGIN')
    dest.import_tasks_from_portable(source.export_tasks_with_history())
    assert conn.in_transaction
    conn.rollback()
    assert dest.list_tasks() == []


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'media/../escape', 'media/sub/file', 'media\\evil', 'media//file'])
def test_unsafe_members_rejected(storage, name):
    source, dest, root = storage
    create_task(source)
    path = write_zip(root / 'unsafe.peppermint', source.export_tasks_with_history(), {name: b'payload'})
    with pytest.raises(archive.ImportError):
        archive.import_archive(dest, path, model='test-model')
    assert not (config.DATA_DIR / 'imports').exists()
    assert dest.list_tasks() == []


def test_symlink_member_rejected(storage):
    source, dest, root = storage
    create_task(source)
    path = write_zip(root / 'symlink.peppermint', source.export_tasks_with_history())
    info = zipfile.ZipInfo('media/link')
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, 'a') as zf:
        zf.writestr(info, '/outside')
    with pytest.raises(archive.ImportError, match='links'):
        archive.import_archive(dest, path, model='test-model')


def test_missing_media_mapping_member_rejected(storage):
    source, dest, root = storage
    create_task(source)
    path = write_zip(root / 'missing.peppermint', source.export_tasks_with_history(), manifest={'/old/image': 'media/missing'})
    with pytest.raises(archive.ImportError, match='mapping'):
        archive.import_archive(dest, path, model='test-model')
    assert dest.list_tasks() == []


def test_duplicate_members_rejected(storage):
    source, dest, root = storage
    create_task(source)
    path = write_zip(root / 'duplicate.peppermint', source.export_tasks_with_history())
    with pytest.warns(UserWarning), zipfile.ZipFile(path, 'a') as zf:
        zf.writestr('tasks.json', '[]')
    with pytest.raises(archive.ImportError, match='duplicate'):
        archive.import_archive(dest, path, model='test-model')


def test_legacy_media_restored_with_warning(storage):
    source, dest, root = storage
    create_task(source)
    path = write_zip(root / 'legacy.peppermint', source.export_tasks_with_history(), {'media/0001-image.png': b'legacy bytes'})
    result = archive.import_archive(dest, path, model='test-model')
    assert any('could not be relocated' in warning for warning in result['warnings'])
    assert next((config.DATA_DIR / 'imports').glob('*/0001-image.png')).read_bytes() == b'legacy bytes'


def test_size_limit_prevents_import_and_partial_export(storage, monkeypatch):
    source, dest, root = storage
    media = root / 'file'
    media.write_bytes(b'too big')
    create_task(source, media)
    path = export(source, root)
    monkeypatch.setattr(archive, 'MAX_MEMBER_BYTES', 2)
    with pytest.raises(archive.ImportError, match='size limit'):
        archive.import_archive(dest, path, model='test-model')
    with pytest.raises(ValueError, match='limit'):
        export(source, root)
    assert dest.list_tasks() == []
    assert list((root / 'exports').glob('*.partial')) == []


def test_extraction_failure_cleans_up(storage, monkeypatch):
    source, dest, root = storage
    media = root / 'file'
    media.write_bytes(b'bytes')
    create_task(source, media)
    path = export(source, root)
    def fail(*args, **kwargs):
        raise OSError('Synthetic disk failure')
    monkeypatch.setattr(archive.shutil, 'copyfileobj', fail)
    with pytest.raises(OSError, match='disk failure'):
        archive.import_archive(dest, path, model='test-model')
    assert dest.list_tasks() == []
    assert list((config.DATA_DIR / 'imports').iterdir()) == []


def test_import_into_source_database_preserves_original(storage):
    source, _, root = storage
    task_id = create_task(source)
    original = source.export_tasks_with_history([task_id])
    path = export(source, root)
    result = archive.import_archive(source, path, model='test-model')
    assert result['task_ids'][0] != task_id
    assert source.export_tasks_with_history([task_id]) == original


@pytest.mark.parametrize('version,accepted', [('0.2.0', True), ('0.2.9', True), ('0.1.9', True),
                                             ('0.3.0', False), ('1.0.0', False), ('bad', False)])
def test_version_compatibility(version, accepted, monkeypatch):
    monkeypatch.setattr(archive, '_peppermint_version', lambda: '0.2.3')
    metadata = {'version': version, 'archive_format': 1}
    if accepted:
        archive._check_compatibility(metadata)
    else:
        with pytest.raises(archive.ImportError):
            archive._check_compatibility(metadata)


def test_tilde_reference_relocated(storage, monkeypatch):
    source, dest, root = storage
    image = root / 'picture.png'
    image.write_bytes(b'image')
    original_expanduser = Path.expanduser
    monkeypatch.setattr(Path, 'expanduser', lambda path: image if str(path) == '~/picture.png' else original_expanduser(path))
    task_id = create_task(source)
    source.add_step(task_id, 'screenshot', {'path': '~/picture.png'}, 'safe', 'saved')
    path = export(source, root)
    image.unlink()
    result = archive.import_archive(dest, path, model='test-model')
    restored = Path(dest.get_steps(result['task_ids'][0])[-1].args['path'])
    assert restored.read_bytes() == b'image'
    assert restored != image


def test_export_snapshot_stays_consistent_across_history_reads(storage, monkeypatch):
    source, _, root = storage
    task_id = create_task(source)
    other = Database(source.path)
    original = source._task_with_history
    def change_after_task_read(conn, task_id, row):
        other.add_message(task_id, 'assistant', {'role': 'assistant', 'content': 'Concurrent message'})
        return original(conn, task_id, row)
    monkeypatch.setattr(source, '_task_with_history', change_after_task_read)
    path = export(source, root)
    with zipfile.ZipFile(path) as zf:
        tasks = json.loads(zf.read('tasks.json'))
        assert len(tasks[0]['conversations']) == 2
    assert len(source.get_messages(task_id)) == 3
