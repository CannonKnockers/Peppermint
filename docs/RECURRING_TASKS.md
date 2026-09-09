# Recurring tasks — implementation and handoff

Updated 2026-09-09. This feature is implemented. No real schedule was created
while developing or testing it. The user requested a local commit of this feature;
check `git log -1` for its commit ID.

## Use it

```sh
peppermint schedule add 3 "daily at 3pm"
peppermint schedule list
peppermint schedule pause 3
peppermint schedule resume 3
peppermint schedule remove 3
```

Use an existing task ID. The original task is the template. Its original idea
is repeated in a new task each time. Follow-up messages, steps, approvals and
results are not copied into the new task. Existing history stays intact.

Accepted phrases: daily/every day, every Monday (any weekday), every weekday,
every weekend, hourly/every hour. Day-based phrases accept `at 3pm`, `at 8:05 am`
or `at 15:30`. No time means midnight. Times use the computer's local timezone.
Unsupported phrases and invalid times return an error instead of guessing.

The original task has a clock icon on task cards and conversation rows. Hover
for its schedule and paused/enabled state. On the Tasks page, use Schedule or
Manage schedule to create, pause, resume, or remove a schedule. The dialog reads
saved state, keeps daemon calls off the GTK thread, blocks duplicate actions,
and displays errors and cron warnings. The CLI remains available.

## Execution rules

- Prefer systemd user timers whenever the user manager is reachable, including
  on Mint. Write `~/.config/systemd/user/peppermint-task-<id>.timer` and `.service`.
  The service calls the absolute Peppermint executable with `run-scheduled <id>`.
- If no user manager is reachable, use crontab and warn in the CLI and a desktop
  notification. Preserve other jobs; manage only the exact task marker suffix.
- The desktop session must be available. Do not enable lingering or catch-up
  after logout/shutdown. The timer uses `Persistent=false`.
- The timer only enqueues work through the normal daemon agent. Each computer
  action still needs normal approval. Scheduling never grants blanket approval.
- Skip an occurrence if the template or previous occurrence is unfinished,
  including queued, running, awaiting approval, or awaiting an answer.
- SQLite claims the occurrence atomically. The schedule stores its most recent
  run ID. Runs are ordinary independent tasks; no fork ancestry is added.
- Pause/remove prevents future occurrences. It does not cancel an existing run;
  use Stop or `peppermint cancel <run-id>` for that.
- Installation saves a paused record first. Failed installation/resume remains
  paused. Repair the OS error, then resume or remove the schedule. Do not fall
  back to cron after a partial systemd installation.
- Changing timing requires remove then add. Archive imports do not install timers.

## File map

- `peppermint/daemon/scheduler.py`: phrase parser, systemd/cron operations,
  executable escaping, paused-on-failure behavior. OS calls have a 20s timeout.
  Constructor accepts fake subprocess, command, paths and notification helpers.
- `peppermint/daemon/db.py`: migration 5, schedule CRUD, atomic occurrence claim,
  schedule information joined into existing task reads.
- `peppermint/common/models.py`: optional `Task.schedule` presentation field.
- `peppermint/common/dbus_api.py`: six schedule methods.
- `peppermint/daemon/main.py`: one bounded scheduling worker, separate from the
  bus loop and model worker. Concurrent schedule requests receive a Busy error.
- `peppermint/cli.py`: schedule subcommands and timer entry point. Also fixes the
  existing fork CLI mismatch (`args.at_step` changed to parser field `args.at`).
- `peppermint/ui/task_row.py`, `task_board.py`: clock indicators.
- `peppermint/ui/manual.py`, `README.md`: user instructions.
- `tests/test_scheduler.py`: parser, migration, OS failures, lifecycle, overlap,
  persistence, history isolation, D-Bus/CLI and rendered GTK tests.

D-Bus methods (input -> output):

| Method | Input | Output |
| --- | --- | --- |
| CreateSchedule | task ID `i`, schedule `s` | schedule JSON `s` |
| ListSchedules | none | array JSON `s` |
| PauseSchedule | task ID `i` | none |
| ResumeSchedule | task ID `i` | none |
| RemoveSchedule | task ID `i` | none |
| RunScheduled | task ID `i` | new task ID `i`, or 0 when skipped |

## UI controls validation — 2026-09-09

- New schedule-controls tests and existing workspace/scheduler tests: 76 passed.
- Full suite: 1479 passed, one parked summary-loading failure, 21.16s.
- Tested create/pause/resume/remove, invalid input, partial failure with saved
  paused state, cron warnings, offline retry, duplicate-click protection,
  late callbacks after destruction, and singleton window integration.
- Live GTK dialog successfully loaded task 40 over D-Bus without creating a
  schedule. No timer or cron changes were made. Compilation/diff checks passed.
- Main UI was left running to preserve drafts. Save/send drafts, choose Hide the
  Peppermint icon in the tray menu, then reopen to load the new buttons.
- New files: ui/schedule_dialog.py and tests/test_schedule_controls.py.

## Original backend validation

- `tests/test_scheduler.py`: **45 passed**. Tests use temporary databases/files
  and mocked systemd/crontab calls. GTK display was available; the clock test ran.
- Full suite: **1470 passed, 1 failed**, 21.11s. The remaining failure predates
  this feature; see below. Log: `/tmp/peppermint-recurring-final-tests.log`.
- `systemd-analyze calendar` accepted daily, weekly, weekday, weekend and hourly
  expressions. `systemd-analyze --user verify` accepted generated units in a
  temporary folder using a harmless executable. No units were installed.
- Python compilation and `git diff --check` passed.
- Local `.venv/pyvenv.cfg` now uses `include-system-site-packages = true` so GTK
  imports work while venv dependencies keep precedence. This file is untracked.
  Do not prepend `/usr/lib/python3/dist-packages` to PYTHONPATH: its older
  typing_extensions conflicts with the venv's pydantic dependency.
- Daemon restarted successfully at 11:20 America/Chicago, schema version 5.
  `peppermint schedule list` returns no schedules. `peppermint health` reports
  Ollama answering, model `qwen2.5:1.5b-instruct`, zero queued jobs.
- Before restart: 40 tasks, none active/waiting. After migration, all eight
  existing data tables matched the backup exactly; only the schema changed.
  Backup: `~/.local/share/peppermint/backups/before-recurring-20260909-112017.db`
  (mode 0600). Main UI was not restarted; clock rendering was checked in GTK tests.

## Parked summary-loading test issue

`tests/test_task_overview.py::test_summaries_include_plans_without_reading_logs_or_messages`
expects overview rows to avoid all conversation reads and use exactly 3 SELECTs.
Existing `Database._row_to_task()` calls `get_messages()` to identify passwords
for display masking. That behavior already existed before scheduling changes.
Schedule data is joined into the existing task query, without per-task schedule
reads. Do not remove password masking to satisfy the old test. The user has parked this issue; do not work on it without new direction.
A future fix should reconcile efficient summary reads with password redaction and test both.

## Resume commands

```sh
cd /home/jesse/Documents/peppermint
git status --short
.venv/bin/python -m pytest tests/test_scheduler.py -q
.venv/bin/python -m pytest tests -q
git diff --check
.venv/bin/peppermint schedule list
.venv/bin/peppermint health
```

Preserve the pre-existing edit in `peppermint/daemon/archive.py`. It was not part
of this recurring-task work and is intentionally excluded from the recurring-task
commit. The user requested a local commit only; no push was requested.
