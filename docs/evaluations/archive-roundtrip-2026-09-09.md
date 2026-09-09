# Archive round-trip audit — 2026-09-09

Result: **4 checks passed, 4 failed**. The archive feature is not yet a reliable
portable backup for tasks with steps or media. This increment checked behavior;
it did not change production code or repair the failures.

All data was synthetic, in temporary SQLite databases and temporary media files.
The audit did not open the live database, execute task actions, or install timers.
Previously uncommitted bug fixes and schedule controls were preserved.

## Reproduce

From the repository root:

```sh
PYTHONPATH=. .venv/bin/python scripts/check_archive_roundtrip.py
.venv/bin/python -m pytest tests/test_export_import.py -q
```

The audit returns exit code 1 while any check fails. The four existing archive
tests pass, but they mostly use mocked export rows; they do not exercise a real
SQLite task through the complete export/import path. The audit intentionally
stays separate from normal pytest collection until its failures are repaired.

## Results

| Check | Result | Evidence |
| --- | --- | --- |
| Real SQLite task with steps: export then import | FAIL | Export raises `ValueError: Archived steps.args must be dict.` |
| Real SQLite task without steps | PASS | New destination ID, conversation content and result preserved. |
| Valid archive import with history | PASS | Conversations, approvals, undo data and timestamps preserved; step/evidence IDs remapped. |
| Transfer screenshots/downloads after deleting originals | FAIL | ZIP contains both files, but import restores neither; history retains the removed source paths. |
| Export-all/import with fork ancestry | FAIL | Imported child 7 points at old ID 1, instead of imported parent 6. |
| Later database failure during multi-task import | FAIL | First task remains inserted after the second import raises an error. |
| Version compatibility | PASS | Patch differences and older same-major minor versions accepted; newer minor, different major and malformed versions rejected. |
| Tilde path media collection | PASS | `~/screen.png` is included in the ZIP when expanded. |

The existing model-mismatch warning test also passed in `test_export_import.py`.
No real approval was exercised; the audit verifies persisted approval history.

## Causes and repair order

1. **Export blocker:** `Database._task_with_history()` copies `steps.args` directly
   from SQLite as JSON text. `_archive_tasks()` requires a dictionary. Decode the
   stored arguments before validating/exporting. Add a real SQLite round-trip
   regression test so mocked dict-shaped fixtures cannot hide this again.
2. **Portable media:** export packages bytes, but provides no source-to-member
   mapping. `_load_archive()` reads only tasks.json and metadata.json; import
   never extracts media or relocates references. Add a media manifest and bounded,
   validated extraction into an import-specific directory, with reference updates
   and cleanup on failed imports. Do not write back to archived absolute paths.
3. **Fork ancestry:** each task is inserted independently with its old
   `parent_task_id`. Build a complete old-to-new task ID map and remap parents;
   define detached-parent behavior for single-task exports. Preserve the DAG.
4. **Partial import:** each `import_task_from_portable()` call commits independently.
   Use one transaction for the complete import, and coordinate media cleanup if
   any task fails. A retry should not leave duplicate partial imports behind.

Start with the export blocker, then repair the remaining round-trip failures.
Password masking works; summary loading remains explicitly parked and unrelated.

## Validation scope

- Repeatable audit: 4 passed, 4 failed, confirmed twice with temporary data.
- Existing `tests/test_export_import.py`: 4 passed in 0.05s.
- No production code changed, so the full application suite was not repeated.
- No live UI/daemon reload or real archive import was performed.
