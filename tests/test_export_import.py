"""Portable archive export/import coverage."""

import json
import zipfile
from pathlib import Path

import pytest

from peppermint.daemon import archive
from peppermint.daemon.db import Database


class FakeExportDb:
    def __init__(self, task_rows):
        self._task_rows = task_rows
        self.last_task_ids = None

    def export_tasks_with_history(self, task_ids):
        self.last_task_ids = task_ids
        return self._task_rows


class FakeImportDb:
    def __init__(self):
        self.rows = []
        self.next_id = 1

    def import_tasks_from_portable(self, tasks):
        return [self.import_task_from_portable(task) for task in tasks]

    def import_task_from_portable(self, task):
        self.rows.append(task)
        imported = self.next_id
        self.next_id += 1
        return imported


def _build_sample_task(task_id: int = 10) -> dict:
    return {
        "id": task_id,
        "idea": "sort files",
        "status": "done",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "result": "done",
        "error": "",
        "question": "",
        "policy_version": 0,
        "steps": [
            {
                "id": 3,
                "task_id": task_id,
                "tool": "read_file",
                "args": {"path": "{path}"},
                "risk": "safe",
                "output": "ok",
                "status": "ok",
                "ts": "2026-01-01T00:00:00+00:00",
                "started_at": "",
                "mutating": 0,
            }
        ],
        "conversations": [
            {
                "id": 11,
                "task_id": task_id,
                "role": "assistant",
                "content": "read results",
                "ts": "2026-01-01T00:00:01+00:00",
            }
        ],
        "approvals": [
            {
                "id": 31,
                "task_id": task_id,
                "step_id": 3,
                "description": "Run this command",
                "reason": "",
                "resolved": 0,
                "approved": 0,
                "ts": "2026-01-01T00:00:02+00:00",
                "token": "",
                "expires_at": "",
                "fingerprint": "{}",
            }
        ],
        "undo": [
            {
                "id": 41,
                "task_id": task_id,
                "kind": "file",
                "target": "/tmp/example",
                "old_value": "",
                "ts": "2026-01-01T00:00:03+00:00",
                "undone": 0,
                "step_id": 3,
            }
        ],
        "run": {
            "task_id": task_id,
            "calls_used": 2,
            "step_start": 3,
        },
        "plan": [
            {
                "step": 1,
                "kind": "verification",
                "evidence_step_id": 3,
            }
        ],
    }

def test_export_archive_writes_json_and_media_files(tmp_path: Path):
    media = tmp_path / "screenshot.png"
    media.write_bytes(b"fake image")
    payload = _build_sample_task(12)
    payload["steps"][0]["args"]["path"] = str(media)

    db = FakeExportDb([payload])

    result = archive.export_archive(db, task_id=12, model="qwen3:8b", base_dir=tmp_path)

    assert result.parent == tmp_path
    assert result.suffix == ".peppermint"
    assert db.last_task_ids == [12]

    with zipfile.ZipFile(result, "r") as zf:
        names = zf.namelist()
        assert "tasks.json" in names
        assert "metadata.json" in names
        assert any(name.startswith("media/") for name in names)

        metadata = json.loads(zf.read("metadata.json").decode("utf-8"))
        assert metadata["archive_format"] == archive.ARCHIVE_FORMAT
        assert metadata["model"] == "qwen3:8b"

        exported_tasks = json.loads(zf.read("tasks.json").decode("utf-8"))
        assert exported_tasks[0]["id"] == 12


def test_import_archive_warns_on_model_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(archive, "_peppermint_version", lambda: "0.1.0")
    payload = [_build_sample_task(8)]
    archive_path = tmp_path / "source.peppermint"
    metadata = {
        "version": "0.1.0",
        "archive_format": archive.ARCHIVE_FORMAT,
        "export_date": "2026-01-01T00:00:00+00:00",
        "model": "qwen2.5:1.5b-instruct",
    }

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("tasks.json", json.dumps(payload))
        zf.writestr("metadata.json", json.dumps(metadata))

    db = FakeImportDb()
    outcome = archive.import_archive(db, archive_path, model="qwen3:8b")

    assert outcome["warnings"] == [
        "Archive model 'qwen2.5:1.5b-instruct' differs from this instance model 'qwen3:8b'. "
        "Imported task history may reference model-specific output.",
    ]
    assert outcome["task_ids"] == [1]
    assert db.rows == archive._archive_tasks(payload)[0]


def test_import_archive_rejects_incompatible_version(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(archive, "_peppermint_version", lambda: "0.1.0")
    archive_path = tmp_path / "bad.peppermint"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("tasks.json", json.dumps([_build_sample_task(1)]))
        zf.writestr(
            "metadata.json",
            json.dumps({
                "version": "1.2.3",
                "archive_format": archive.ARCHIVE_FORMAT,
                "export_date": "2026-01-01T00:00:00+00:00",
                "model": "qwen3:8b",
            }),
        )

    with pytest.raises(archive.ImportError):
        archive.import_archive(FakeImportDb(), archive_path, model="qwen3:8b")


def test_database_import_remaps_history_ids_and_retest_messages():
    db = Database(":memory:")
    source = _build_sample_task(77)
    source["steps"][0]["id"] = 8
    source["run"]["step_start"] = 8
    source["conversations"] = [
        {
            "id": 11,
            "task_id": 77,
            "role": "assistant",
            "content": json.dumps({
                "name": "request_retest",
                "content": json.dumps({
                    "evidence_step_id": 8,
                    "label": "verify",
                }),
            }),
            "ts": "2026-01-01T00:00:01+00:00",
        }
    ]
    source["approvals"][0]["id"] = 44
    source["approvals"][0]["step_id"] = 8
    source["undo"][0]["id"] = 55
    source["undo"][0]["step_id"] = 8
    source["plan"][0]["evidence_step_id"] = 8

    new_task_id = db.import_task_from_portable(source)
    assert new_task_id == 1

    steps = db.get_steps(new_task_id)
    assert len(steps) == 1
    assert steps[0].id == 1

    row = db.connection().execute(
        "SELECT step_id FROM confirmations WHERE task_id = ?",
        (new_task_id,),
    ).fetchone()
    assert row["step_id"] == 1

    run = db.connection().execute(
        "SELECT step_start FROM task_runs WHERE task_id = ?",
        (new_task_id,),
    ).fetchone()
    assert run["step_start"] == 1

    plan = json.loads(db.connection().execute(
        "SELECT steps FROM task_plans WHERE task_id = ?",
        (new_task_id,),
    ).fetchone()["steps"])
    assert plan[0]["evidence_step_id"] == 1

    message = db.connection().execute(
        "SELECT content FROM messages WHERE task_id = ? AND role = 'assistant'",
        (new_task_id,),
    ).fetchone()
    content = json.loads(message["content"])
    payload = json.loads(content["content"])
    assert payload["evidence_step_id"] == 1

    second_import = db.import_task_from_portable(source)
    assert second_import == 2
