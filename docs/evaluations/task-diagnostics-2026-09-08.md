# Task system and visual diagnostics — September 8, 2026

This phase adds Tasks/Conversations/Diagnostics navigation, a paginated task
overview, opt-in resource graphs and advanced process/device inspection. It
preserves the previous conversation, approval and typed-retest implementation.

## Validation scope

The database tests cover global counts independent of pagination, every filter,
Unicode/literal wildcard search, consistent read snapshots, page bounds and the
new TaskOverview D-Bus method. No migration was needed. Reader tests block a
fake daemon call and verify background work/main-thread delivery, supersession,
bounded pending/completed queues, callback failure recovery and teardown.

GTK tests cover switching pages, retaining composer/follow-up drafts, opening
older tasks, stale filter responses during debounce, selected-task context and
monitoring visibility. Diagnostic tests exercise first/reset/missing readings,
disk/network accounting, process reuse/exit, filtering/sorting, graph gaps,
bounded history, stop/resume, coalesced delivery and fatal worker errors.

The collector completed three read-only samples on this desktop in 68–80 ms
each, with no collection errors. It observed 32 logical CPUs, a physical NVMe
disk, two physical network interfaces, an NVIDIA RTX 3060 Ti and a thermal-zone
sensor. It scanned 560 processes and returned the bounded subset of 256. Derived
rates were null on the first sample and finite/nonnegative afterward. These
observations are a short idle-session check, not a prolonged load benchmark or
a simultaneous model-inference test. No game was inspected or repaired.

The process list is a sampled subset, not a full application inventory. Search
and sorting apply to that subset; shared RSS is not summed into system RAM.
GPU telemetry currently supports NVIDIA only, using nvidia-smi; unsupported
devices remain unavailable. Thermal-zone coverage varies by kernel/driver.
Physical devices/interfaces exclude partitions and virtual stacks to avoid
duplicate totals. A physical network total is not per-application traffic.

## Evidence semantics and limits

CPU rates exclude guest double-counting and idle/iowait. Disk sectors use 512
bytes; swap counters use the system page size. Process CPU is relative to one
logical core. PSI totals are microseconds. These definitions follow the Linux
kernel's [proc documentation](https://docs.kernel.org/filesystems/proc.html),
[disk statistics](https://docs.kernel.org/admin-guide/iostats.html),
[network statistics](https://docs.kernel.org/networking/statistics.html) and
[pressure interface](https://docs.kernel.org/accounting/psi.html).

Sampling is off at startup. Start enables bounded read-only collection without
model inference; hiding or leaving the view pauses it. Explicit Pause clears
the resume intent. Late callbacks are discarded and a resumed sampler starts
fresh rate baselines. History holds at most 150 metric samples; only the current
process inventory is retained. No samples are written to the conversation DB.

Checklist completion is recorded work, not an estimated percentage of remaining
time. Task context displays task creation/update and step creation timestamps;
it does not invent completion times or assign a machine-wide spike to a task.
Interval selection, plot-aligned event markers, diagnostic sharing into a prompt,
process termination and administrative screen control remain future work.

## Visual review

Real GTK previews use deterministic illustrative task/metric fixtures and never
start a collector. Basic and Advanced layouts were inspected at 660px content
width and larger desktop sizes. Advanced moves to its process list; Basic
returns to the graphs. Native TreeView header buttons are drawn separately by
the preview helper because root-widget Cairo capture omits their GDK windows.

- [Task overview](peppermint-tasks.png)
- [Basic diagnostics](peppermint-diagnostics-basic.png)
- [Advanced diagnostics](peppermint-diagnostics-advanced.png)

## Final validation and reload

Recovered the interrupted finalization on September 8, 2026. Review found that
changing the sampling cadence from 5s to 2s could redraw valid older readings as
gaps. Charts and task-time labels now preserve the gap classification recorded
when each sample arrived. A regression checks both directions: continuous
history stays continuous, and a real recorded gap stays visible.

- `.venv/bin/python -m pytest tests -q`: **1,201 passed in 6.87 seconds**,
  including GTK tests using the current desktop display. The CI-style
  `xvfb-run` command was unavailable locally; no tests were skipped in the
  successful desktop run.
- Python compilation and `git diff --check` passed. The three linked previews
  were generated from the GTK fixture scenes and visually reviewed.
- A fresh live Monitor check delivered three error-free samples with 2.087s
  and 2.070s between sample starts. First-sample rates stayed unavailable. The
  final inventory scanned 567 processes and returned the bounded 256. The
  worker joined successfully on Stop; no additional callbacks arrived during
  the following 2.3s observation.
- A separate GTK smoke check used the real read-only daemon API: all 35 tasks
  loaded, monitoring remained off initially, Start displayed three error-free
  live samples, and hiding stopped delivery for a 2.3s observation. A 50ms GTK
  heartbeat had a maximum observed gap of 97.5ms during this short run. This is
  a desktop smoke check, not a prolonged or simultaneous-inference benchmark.
- At 20:09 America/Chicago, the backend was reloaded as
  `peppermint-daemon.service` PID 350835 and the background UI as
  `peppermint-window.service` PID 350885. Both services were active and the
  window owned its D-Bus name. Health returned healthy with an empty queue and
  the existing `qwen3:8b` model. All five TaskOverview filters responded.
- Read-only hashes of all nine database tables matched before and after
  reload. All 35 tasks remain: 30 done, 2 failed, 2 cancelled and 1 awaiting
  approval. Task 14 still awaits approval 42; the older unresolved confirmation
  row 3 also remains unchanged. Database quick-check returned `ok`. No active
  task or approval was executed for validation.

Ollama was not restarted or reconfigured in this recovery; its service remained
active with PID 340573 and zero automatic restarts. The work remains uncommitted.
Controlled application management is the next substantive increment in the
[roadmap](../ROADMAP.md).
