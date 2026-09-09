# Latest increment — sidebar plugin management, 2026-09-09

Plugins now opens inside the main window from the sidebar. It lists installed
plugins, state, loaded tools, file location, and saved load/crash details. Users
can enable, disable and refresh plugins. Failed enables stay disabled; successful
enables clear old errors. Task approval behavior is unchanged.

Validation: 34 focused checks passed. Full suite: 1487 passed, one parked
summary-loading failure. Live GTK/D-Bus read passed; the daemon was reloaded and
is healthy. No installed plugins or live plugin changes. Main UI needs a restart
to load the new sidebar page. Changes are uncommitted; see NEXT_SESSION.md.

Archive repairs and summary loading remain parked. Password masking works.

---

# Latest check — archive round trip, 2026-09-09

The audit found four concrete portability failures: real SQLite step export,
media restoration, fork parent remapping, and rollback of multi-task imports.
Four other checks passed. Existing archive unit tests pass but did not cover the
complete real-database path. Production code and live data were unchanged.

See [archive audit](evaluations/archive-roundtrip-2026-09-09.md) and
`scripts/check_archive_roundtrip.py` for reproducible evidence and the repair order.
Password masking works; summary loading remains parked.

---

# Latest increment — schedule UI controls, 2026-09-09

Tasks now have Schedule / Manage schedule buttons for creating, pausing, resuming
and removing recurring schedules. The dialog stays responsive during daemon
calls and displays saved state, failures and cron warnings. README and User
manual include the controls. See NEXT_SESSION.md for the current handoff.

Validation: 76 focused tests passed; full suite 1479 passed, one parked
summary-loading test failure. Live GTK-to-D-Bus loading passed. No schedules
were created and no daemon or database logic changed. Main UI still needs a
restart to load the controls; it was left running to preserve drafts.
These UI changes are uncommitted. Password masking and summary loading are unchanged.

---

# Latest increment — recurring tasks, 2026-09-09

Recurring tasks now support natural-language timing, systemd user timers with
cron fallback, pause/resume/remove commands, and clock indicators. The daemon
is loaded with schema 5. No actual recurring task was installed during this work.

See [RECURRING_TASKS.md](RECURRING_TASKS.md) for the current implementation,
commands, tests and Spark handoff. Feature tests: **45 passed**. Full suite:
**1470 passed, 1 pre-existing failure** in the overview/password-masking test.
The user requested a local commit of this increment; check `git log -1` for its ID.
No push was requested. The earlier completed baseline below is historical.

---

# Project checkpoint — 2026-09-08

The task system, visual diagnostics, sidebar/manual and recovery/session-control
increments are implemented, validated and loaded. **This is the completed
baseline prepared for GitHub publication as a new commit on `main`.** The user
authorized Commit & Push after the compaction checkpoint. Use
[NEXT_SESSION.md](NEXT_SESSION.md) as the compact handoff and Git for current
commit/publication state.

A wordless peppermint emblem opens a full-height sliding left sidebar, including
access to a searchable user manual and Recovery. The user explicitly wants a
sidebar, not a dropdown.
See [menu/manual validation](evaluations/menu-manual-2026-09-08.md) and the earlier
[task/diagnostics validation](evaluations/task-diagnostics-2026-09-08.md).
The latest [recovery validation](evaluations/recovery-2026-09-08.md) records the
installed shortcut, administrator check, session controls and frozen-window test.

The [dialogue evaluation](evaluations/dialogue-2026-09-08.md) preserves remaining
model failures and successes. The earlier [Hugging Face checkpoint](evaluations/hf-protocol-2026-09-08.md)
preserves provenance and comparison results; those evaluations were not repeated
and no model was promoted in this task-system phase.

## User direction

- Preserve the charcoal/mint material design and smooth animations. The user
  replaced the prompt-guide button with a wordless peppermint main-menu button;
  global navigation and Refresh now live in a sliding left sidebar. The built-in User manual
  includes operating instructions, search and the ten copyable examples.
- Preserve compact native minimize/maximize/close controls at the top-right
  corner. Close hides the UI; the daemon continues.
- Build strong Linux troubleshooting, starting with Steam / Proton, including
  interactions with other operating systems and applications.
- Use Hugging Face where helpful. CLI login was verified in the previous phase;
  this is not an in-app account connection or model training.
- The task system and basic/advanced visual diagnostics now have a first increment.
  Exact-process recovery and an independent fullscreen emergency UI are also
  implemented. Broader application grouping and administrative desktop automation
  remain future work.
  See [ROADMAP.md](ROADMAP.md) for the proposed breakdown.
- Work autonomously, stopping at a clear checkpoint before extending the session.

## Recovery and session controls — latest increment

- Ctrl+Alt+Delete now launches the separate fullscreen `org.peppermint.Recovery`
  GTK application through Cinnamon custom0. Existing custom1 is preserved. Mint's
  original logout binding moved to Ctrl+Alt+Shift+Delete, verified unused first.
  Original values are in `~/.local/share/peppermint/recovery-shortcut-backup.json`.
- Recovery is independent of the daemon, model and normal window. The sidebar
  and `peppermint recover` can launch it too. A live XTest shortcut opened it in
  0.455 seconds above a disposable frozen GTK window. Repeated activation reused
  the same window; Escape closed it with search focused. The fixture was resumed
  and removed. This does not prove takeover during a frozen compositor/kernel.
- A bounded process snapshot exposes explicit per-process stop controls. TERM
  requires confirmation; KILL requires an observed timeout and a separate
  confirmation. PID/start ticks/UID plus pidfd identity checks prevent reused-PID
  targeting. Known critical system/display/session processes are protected.
- The standalone helper is installed at
  `/usr/local/libexec/peppermint-recovery-helper`, root:root 0755. Source and
  installed copies match. An authenticated `--check` returned euid 0 and pidfd
  availability without signaling any process. No permanent passwordless grant,
  general command runner or elevated GTK window was introduced.
- Session controls include Lock, Switch user, Log out, Suspend, Restart and Shut
  down on this host. Hibernate is hidden because Mint reports it unavailable.
  Logout/restart/shutdown use Mint's native confirmation and inhibitor handling;
  other session actions require explicit Recovery confirmation. Session calls
  have a three-second budget; uncertain outcomes yield the screen without retry.
  No live session-changing action was used for testing.
- **1,387 tests passed in 16.75 seconds**. Compilation, shell syntax, whitespace,
  helper integrity, live capabilities and fixture previews passed. GTK tests
  cover responsive workers, separate force confirmation, auth cancellation,
  native-dialog handoff and reachable controls at 660×560 with scrolling.
- README and the searchable User manual include setup, restoration, process
  recovery, session actions and actual freeze limits. Ten copyable examples remain.

## Task system and visual diagnostics

- The window opens to Tasks. The peppermint menu navigates among Tasks,
  Conversations, Diagnostics and User manual, with New conversation, Refresh
  and Hide window actions. Native corner controls and material styling remain.
  The page name appears beneath the title; the separate tab row was removed.
- TaskOverview is a new read-only D-Bus API: global status counts, literal
  case-insensitive search, five filters, pages of up to 100, and joined plan
  summaries. The UI uses pages of 40. No schema migration or task mutation.
- Task cards show status, update time and recorded checklist completion.
  Open/Review loads the existing conversation, including old tasks outside the
  recent page. Open rows and drafts survive refresh/tab changes; unused closed
  rows can be released. No time-remaining or verified-repair claim is inferred.
- UI task reads run on a separate bounded worker, coalesced by request key;
  generations reject stale filter/detail responses. Refresh also updates open
  detail. Existing mutation calls retain their previous D-Bus behavior.
- Monitoring starts off, with explicit Start/Pause and 2s/5s sampling intervals.
  Leaving Diagnostics or hiding the window pauses collection and late delivery;
  returning resumes only if Start remains enabled. Destruction stops the worker.
  A current bounded probe may still finish; no subsequent probes are started.
- Basic charts show CPU, memory, NVIDIA GPU, physical-disk activity, physical
  network activity and swap. Advanced opens the searchable/sortable process
  subset, per-core readings, load, pressure, temperatures, swap rates and device
  detail. PID plus process-start ticks bind a selected process across refreshes.
- The sampler reads bounded /proc and /sys data plus a bounded nvidia-smi query.
  It does not read process command lines/environments or invoke a model. Missing
  sensors, first samples, resets and resume gaps remain unavailable, not zero.
  Process CPU is a percentage of one core and can exceed 100%.
- History is in memory only, capped at 150 metric samples; process inventories
  are current-only. Charts offer 60s/5m windows. Process collection is bounded to
  2,048 scanned PIDs/0.25s by default, with up to 256 results ranked by CPU then
  RSS; UI search/sort only that subset. Permission-limited process I/O stays blank.
- Task context lists task-created/last-updated and step-recorded timestamps,
  identifying times outside the sampled window or in gaps. A step timestamp is
  its creation time, not completion. No event overlays, interval selection,
  persistent history or automatic diagnosis are implemented in Diagnostics.
  Process termination is now available in the separate Recovery window.
- Sampling-cadence changes preserve historical gap classifications in both
  charts and task-time labels; they cannot invent or erase earlier gaps.
- Final validation: **1,201 tests passed in 6.87 seconds**, including GTK tests.
  Compilation, whitespace checks, fixture previews and live monitoring/GTK
  smoke checks passed. Full scope and reload observations are in the phase report.
  The sidebar/manual update passes **1,207 tests in 10.09 seconds**, including
  Escape/outside/close dismissal, full-height layout and disabled animations.

## Previous implemented baseline

- The earlier left guide is now a full-height left sidebar with a 280ms slide
  animation and a full-page manual. The peppermint icon, close arrow, Escape
  or dimmed outside area closes it. It overlays without resizing the workspace;
  selecting a destination closes the sidebar. Desktop animation preferences apply.
- Steam/Proton diagnostics and eleven sourced Linux/cross-OS reference notes are
  loaded. Inspections still require approval; bundled reference retrieval does
  not access user files. Missing probes do not prove runtime failure.
- Native Ollama tool results use tool_name. Current-turn empty-response recovery
  reaches the model; historical internal nudges remain hidden. Native thinking
  stays in protocol history, separate from visible conversation.
- Native text replies require a separate schema-constrained answer/ask_user
  decision, without tools or thinking. A needed question enters awaiting-input
  and persists ordinary reply/choice history; an answer preserves its text.
  Invalid, overlong, malformed or truncated decisions fail visibly. The extra
  model call uses the persistent task budget and supports cancellation.
- The routing role is separate from native action instructions. It considers
  needed information even when a draft embeds a request inside a longer plan.
  This remains a model decision, not a proof of intent or diagnostic correctness.
- Plans distinguish inspection/action/verification. Current plan positions are
  explicitly numbered and refreshed each loop; evidence IDs are separate.
- request_retest asks for an exact unfinished verification target. Typed Passed,
  Still failing and Not tested buttons use a separate D-Bus/CLI channel. Prose,
  JSON-looking answers and logs cannot create a pass. Only a current Passed
  report completes the bound target, labelled User-reported pass.
- Retests bind task, request ID, plan position/description and app-generated
  target identity. Stale/duplicate/cancelled/changed-target submissions fail
  closed. Subsequent approved computer changes expire reports in that task.
  Report, timestamp, transcript, plan and queued continuation commit atomically.
  There is no database migration. [Preview](evaluations/peppermint-retest.png).
- Nanosecond approval fingerprints from the previous phase are now loaded.
  Older filesystem approvals may require a fresh exact-action approval. Existing
  saved approval rows were not edited or executed.
- Previous dialogue-phase validation: **1,117 tests passed in 4.31 seconds**.

## Real-model validation and limits

- Configured Qwen3:8b: native dialogue 10/11; supplied text drafts 11/11;
  fresh native holdout 4/4; guarded support workflows 3/3; typed retest workflows
  2/2. Fixtures never inspected or repaired a real game and never submitted a
  real user's retest outcome.
- One ask-only Steam retest prompt still proposed diagnostics, then incorrectly
  questioned the supplied AppID after the guard blocked it. Some passing
  questions omitted useful details. Both guarded game workflows paused for
  more information; their protocol scores do not validate diagnostic accuracy.
- The initial typed retest run chose the right tool but repeatedly used the
  wrong plan index. Explicit plan numbering and informative validation errors
  fixed the two recorded cases. The app does not silently substitute a target.
- Keep injection, stale logs, denial interpretations, unknown Windows paths,
  incomplete scope, and unsupported graphics-driver conclusions as regressions.
  Earlier model reports preserve these unresolved weaknesses.
- Reports retain the initial schema failure, baseline, interrupted OOM run,
  retest index failure and final runs. Dialogue/holdout snapshots predate the
  final plan-numbering improvement; the typed retest artifact freezes that final
  code. See the evaluation for exact scope and provenance limits.

## Local runtime and account state

- Backend last reloaded at 20:09 America/Chicago: peppermint-daemon.service PID
  350835. UI last reloaded at 21:24 via peppermint-window.service PID 361504 and
  presented with the latest sidebar/manual. The backend remained PID 350835.
  Health, TaskOverview and window bus ownership passed after this UI reload.
- All 35 saved tasks remain: 30 done, 2 failed, 3 cancelled. No queued, running,
  planning or awaiting-input/confirmation tasks are currently reported. This is
  the latest read-only observation; it supersedes the earlier pending Task 14
  checkpoint. All nine database table hashes matched across this UI reload.
  No live conversation or pending task action was used for recovery testing.
- Ollama's default 8 GiB host prompt-snapshot cache caused an OOM kill during
  extended evaluation; the service automatically restarted. A local user-service
  override now sets LLAMA_ARG_CACHE_RAM=512 in
  ~/.config/systemd/user/ollama.service.d/peppermint-prompt-cache.conf.
  Runner logs confirm 512 MiB while retaining 16k context. This affects all runners
  under that local service, reduces cache reuse, and is not a total-memory cap.
  Final model checks completed with no further OOM restart.
- Ollama PID 340573 was healthy after the cache change. Qwen3:8b remains configured.
  Qwen3.5:4b and peppermint-qwen35-9b-eval:latest remain installed. The 9B HF cache
  and Ollama import from the previous phase remain; no download, promotion,
  training, upload, paid service or private repository was used in this phase.
- docs/hub-api-endpoints.json is the user's supplied public API specification.
  Publication review replaced one public credential example with an obvious
  placeholder; all other parsed values are unchanged. It does not authenticate
  an account, and no private token was added to the transcript or repository.

## Resume here

1. Read NEXT_SESSION.md. The user requested a compaction checkpoint, then
   authorized publishing the completed changes to `jescolmax/Peppermint` on
   `main`. Check Git for the current commit and remote state; preserve the
   completed feature set and wait for the next feature direction.
2. Do not rebuild the task board, collectors, graphs, sidebar/manual or Recovery.
   Broader application grouping, interval selection, chart event markers and
   passing selected measurements into a conversation remain separate future
   improvements. Full administrative desktop automation is not implemented.
   Keep sampling overhead bounded on this 16 GiB desktop and recheck the Ollama
   cache cap before extended model runs.
3. Preserve known model failures as regressions. Diagnostic visuals must separate
   measurements, missing/stale observations and model interpretations. User
   retest reports remain distinct from independent measurements.
4. A real game repair still needs a concrete user-selected failure; Steam / Proton
   is known, but no actual failing game has been named. This is not a blocker for
   implementing the task system or visual diagnostics.
5. Runtime service PIDs are checkpoint observations and should be
   rechecked before future reloads or model evaluation.
