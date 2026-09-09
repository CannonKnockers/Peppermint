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
