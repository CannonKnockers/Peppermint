# Current handoff — Plugins in the sidebar

Updated 2026-09-09. Plugin management is implemented and uncommitted.

The sidebar has a Plugins destination inside the existing window. The page
shows enabled/disabled/not-loaded state, loaded tools, expandable file location,
and saved failure details. Enable/Disable actions run off the GTK thread and
reload daemon state on success or failure. Refresh discovers new files and
updates state after crashes or CLI changes. Duplicate actions are blocked;
unavailable saved state leaves old actions disabled until Refresh succeeds.

Backend: PluginState now persists an errors mapping (backward compatible with
older state files). ListPlugins includes an error field. Load and runtime
failures record details; failed enables remain disabled; successful enables
clear errors. Older failures without saved details cannot be reconstructed.
No changes were made to tool approval rules.

Files: ui/plugins_view.py, ui/main_menu.py, ui/window.py, daemon/plugins.py,
README, User manual, tests/test_plugins_view.py, tests/test_plugins.py, and the
sidebar integration test in tests/test_workspace_ui.py.

Validation: 34 focused tests passed; full suite 1487 passed, one parked
summary-loading failure (21.63s). Diff and compilation checks passed. The idle
daemon was restarted and is healthy. A live GTK Plugins page read ListPlugins
successfully. Zero installed plugins; no live enable/disable actions performed.
The main UI was left running to preserve drafts. Save/send drafts, choose Hide
the Peppermint icon in its tray menu, then reopen to load the new sidebar page.

Preserve all preceding uncommitted bug fixes, schedule controls and archive-audit
files. Archive repairs and summary loading are parked. Password masking works.
No commit or push was performed in this increment.

---

# Current handoff — archive round-trip audit

Updated 2026-09-09. The archive check is complete: **4 passed, 4 failed**.
Read [the audit](evaluations/archive-roundtrip-2026-09-09.md) for exact evidence,
causes and repair order. Reproduce with:

```sh
PYTHONPATH=. .venv/bin/python scripts/check_archive_roundtrip.py
```

Failures: real step export rejects stored JSON arguments; imported media is not
restored; fork parent IDs are not remapped; a later import failure leaves earlier
tasks committed. Existing mocked archive tests still pass (4 tests).
No production code was changed for the audit. No live data was imported.
Next repair starts with decoding step arguments during export, then the remaining
portable-media, ancestry and transaction issues. Summary loading remains parked.

Preserve all existing uncommitted four-bug-fix and schedule-control changes.
The preceding UI handoff below still records its validation and reload needs.

---

# Current handoff — schedule UI controls

Updated 2026-09-09. Schedule controls are implemented but uncommitted.
The recurring backend was committed as d62fda4. No push was requested.

On the Tasks page, each task has Schedule or Manage schedule. The dialog creates,
pauses, resumes and removes schedules through the existing D-Bus methods. It
loads saved state, runs calls off the GTK thread, blocks duplicate actions,
and shows daemon errors and cron fallback warnings. No backend logic changed.

Files: ui/schedule_dialog.py, ui/task_board.py, ui/window.py, ui/manual.py,
tests/test_schedule_controls.py, and the window integration test.
README and RECURRING_TASKS.md explain the controls.

Validation: 76 focused tests passed. Full suite: 1479 passed, one existing
summary-loading failure, 21.16s. Compilation and diff checks passed. A live GTK
dialog read task 40 from the running daemon. No schedules were created.

The existing main UI process was not restarted, to preserve unsent text. To load
the new controls, save/send drafts, use the tray menu's Hide the Peppermint icon
(which quits the UI), then reopen Peppermint. Closing the main window only hides
it and does not reload code. The daemon does not need a restart for these controls.

Preserve the earlier four bug fixes in archive.py, db.py and llm.py. Password
masking works. The user explicitly parked summary-loading work; do not treat its
existing test failure as the next task. No masking or summary query changes were
made for this UI increment.

The older checkpoint below is historical; do not use its PIDs, test counts or
publication instructions as current state.

---

# Compact handoff — GitHub commit baseline

Updated 2026-09-08, 21:24 America/Chicago.

The user requested a compact checkpoint, then authorized committing to GitHub.
This file records the completed baseline prepared for a new commit and push to
`origin/main` at `https://github.com/jescolmax/Peppermint`.

## Next action

Work in `/home/jesse/Documents/peppermint`, branch `main`. This increment builds
on `418c4b3` (`Install Cinnamon settings dependencies in CI`). Use `git status
--short`, `git log -1` and `git branch -vv` to check the current commit/publication
state before doing more work. Preserve the complete accumulated feature set.
Wait for the user's next feature direction; do not restart model evaluations.

## Completed and loaded

- Task board, conversation/retest improvements and basic/advanced diagnostics.
- Wordless peppermint emblem opens a sliding left sidebar, never a dropdown.
  It contains global navigation/actions, Recovery and searchable User manual;
  the manual preserves ten copyable examples. Charcoal/mint theme retained.
- Independent fullscreen Recovery opens with Ctrl+Alt+Delete or `peppermint
  recover`. Cinnamon logout moved to Ctrl+Alt+Shift+Delete. Original settings:
  `~/.local/share/peppermint/recovery-shortcut-backup.json`.
- Exact-process termination request, separately confirmed force stop after a
  timeout, pidfd identity checks and known-critical-process protection.
- Restricted administrator helper installed root:root 0755 at
  `/usr/local/libexec/peppermint-recovery-helper`; authenticated read-only probe
  returned euid 0. The GTK window stays unprivileged. No passwordless grant.
- Session controls: Lock, Switch user, Log out, Suspend, Restart, Shut down.
  Unsupported Hibernate hidden. Mint confirmations/inhibitors retained.
- Recovery can help with frozen applications; it needs responding Cinnamon
  keyboard handling/display/kernel. It cannot guarantee a hard-freeze takeover.

## Validation and runtime

- Full suite: **1,387 passed in 16.75s**; compilation, shell syntax and diff
  whitespace checks passed. UI tests reach controls at 660×560 using scrolling.
- Publication review added the required `python3-gi-cairo` package to CI and
  installation instructions, and a Cairo preflight to the installer.
  Installer success/missing-dependency paths and 28 affected GTK tests passed.
  The public API schema's single credential example was replaced with a clear
  placeholder; reviewed artifacts contain no detected private credentials.
- Real Ctrl+Alt+Delete activated fullscreen above a frozen disposable GTK window
  in **0.455s**. Repeat reused the same window; Escape closed it. Fixture cleaned
  up. No live logout, power, sleep, lock or switch-user action ran in tests.
- UI reloaded 21:24: `peppermint-window.service` PID **361504**. Daemon stayed
  **350835**, model `qwen3:8b`, healthy. These PIDs are snapshots; recheck if needed.
- All **35** tasks remain (30 done, 2 failed, 3 cancelled), none active/pending.
  Hashes of all nine database tables matched before/after UI reload.
- Model evaluation limitations and Ollama's 512 MiB prompt-cache override are
  preserved in PROJECT_STATUS.md; no model promotion or new evaluation occurred.

Read [PROJECT_STATUS.md](PROJECT_STATUS.md) for wider context and
[recovery validation](evaluations/recovery-2026-09-08.md) for implementation/test
details. README and User manual contain setup and shortcut restoration commands.
