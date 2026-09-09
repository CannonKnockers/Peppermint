# Peppermint direction

Updated September 8, 2026 from the user's requested direction. Peppermint is
growing from a conversational Linux helper into a desktop task manager with
diagnostic graphics, application management, and administrative screen control.
The stages below separate the implemented foundation from further planned work.

The user has approved the current baseline and selected the task system with
basic and advanced visual diagnostics as the next major implementation phase,
after compaction. The numbering below groups related work; it does not require
resolving every model-reliability issue before starting the task system.

## 1. Reliable troubleshooting

Start with Steam/Proton, then broaden Linux application support and interactions
with Windows, macOS and other Linux systems. Identify the exact application,
runtime/OS, target machine and symptom; gather relevant measurements; consult
reviewed sources; propose a change; verify the original failure after it runs.
Keep missing observations distinct from failed components and preserve saves,
configuration and credentials. Track sources and review dates as documentation
changes. Current implementation and remaining failures are in
[PROJECT_STATUS.md](PROJECT_STATUS.md).

Model selection uses measured Peppermint workflows. The
[Hugging Face research](research/hugging-face-models-2026-09-08.md) describes public
model access and compatible local formats. Compare installed candidates before
downloading larger ones. Source retrieval updates the context supplied to the
model; fine-tuning model weights is a separate, later project using reviewed
examples and independent evaluation.

Completion criterion: the model reliably clarifies missing targets, handles
tool results as evidence, uses sources when requested, and distinguishes
inspection, an applied change, and a verified outcome in multi-step tests.

## 2. Task system and visual diagnostics — first increment implemented

The first increment now provides Tasks/Conversations/Diagnostics navigation,
global task counts and filtered pagination, opt-in bounded sampling, six basic
resource graphs, advanced per-core/device/pressure/temperature detail, and a
searchable process subset with stable PID/start-time selection. A selected task
shows creation/update and recorded-step timestamps alongside measurements.
Start/Pause, hiding, view changes and restart gaps are covered by lifecycle tests.
Measurements remain in memory and do not invoke the model. Current scope and
validation are in [PROJECT_STATUS.md](PROJECT_STATUS.md).

Remaining work includes full application grouping, selectable time intervals,
plot-aligned event markers, exporting/sharing a selected measurement with a
conversation, broader GPU/sensor adapters and controlled application management.
The task timestamp list provides context; it does not attribute system usage to
a task. There is no event brush or persisted monitoring history yet.

Build a task overview and detail views around the existing queue, task states,
conversation history, plans, approvals and retest evidence. Distinguish Peppermint
work from the operating system's application/process list. Keep the current
conversation interface available and carry the charcoal/mint design into the
new views. The following basic/advanced split describes the broader target.

Basic diagnostics: readable live CPU, memory/swap, disk activity, network and
GPU graphs where supported; an at-a-glance resource summary; and a searchable,
sortable application/process list. Show units, sample times and unavailable
measurements clearly. Task cards should make progress, waiting questions,
pending approvals and completion outcomes easy to see.

Advanced diagnostics: per-core CPU detail, per-process resource inspection,
GPU memory/utilization and available temperatures, storage and network detail,
plus time-aligned measurements and activity events for a selected task. Allow
the user to inspect a time interval and relate observed changes to recorded
actions. Start from available collectors and supported measurements rather than
inventing sensor values or treating correlation as a confirmed cause.

Graphs must display collected values and sample times; unavailable sensors and
gaps must remain visible. Give the user control over sampling and bound retained
history and monitoring overhead. Let a conversation refer to a selected process
or a selected time interval rather than guessing from a screenshot or app name.

Completion criterion: graphs match fixture measurements and live samples, the
view stays responsive during model inference, and stopping monitoring stops
collection. Validate actual machine impact before enabling continuous sampling.

Ship in reviewable increments: task overview and bounded collection, basic
graphs/process list, advanced detail and task timelines, then application
management controls. Administrative screen control retains its own later
implementation and validation scope below.

## 3. Application management and cancellation — process recovery implemented

The separate Recovery window now offers bounded process search, exact
PID/start-time/UID selection, a confirmed termination request and a separately
confirmed force stop after timeout. pidfds protect against PID reuse. Known
critical processes are blocked and other-user actions use Mint authentication
through a restricted installed helper. Controlled child-process tests cover
exit, timeout, identity changes and permission failures. Application grouping
and integration with selected diagnostic measurements remain future work.

Make stopping a Peppermint task distinct from closing an application. Show the
selected application/process and its identity, attempt graceful closure first,
and offer forced termination separately when needed. Recheck identity before
acting so a reused process ID cannot target a different application. Report
whether a stop was requested, observed, or still pending; an inference request
ending does not establish that an application exited.

Completion criterion: fixture and controlled-process tests cover graceful exit,
an unresponsive process, permission failure, process replacement, and task
cancellation during execution. Integrate with the existing exact-action approval
flow; do not treat termination as undoable.

## 4. Administrative screen control — emergency entry point implemented

Ctrl+Alt+Delete now opens independent fullscreen Recovery; Mint's logout shortcut
moves to Ctrl+Alt+Shift+Delete. The sidebar also launches Recovery. It combines
process controls with Mint's supported session actions and yields to native
confirmation/authentication dialogs. A frozen disposable application was used
to verify shortcut activation and foreground/fullscreen behavior. This requires
a responding desktop keyboard service, display server and kernel; it is not a
kernel-freeze takeover mechanism. See [validation](evaluations/recovery-2026-09-08.md).

Further desktop automation begins with selected-window inspection, explicit screenshot
input where useful, accessible control targeting, and a visible interruption
control. Administrative actions should identify their scope and integrate with
the operating system's privilege flow. A vision-capable model alone does not
capture screens, click controls or gain administrative privileges.

Remote desktop administration is a later scope decision requiring a target
machine, connection method and authentication flow. The current reference
library's cross-OS knowledge does not grant access to another device.

Completion criterion: prove observation and action behavior on controlled test
windows, including focus changes, stale screenshots, wrong-window prevention,
interruption and denied privilege requests, before expanding to real applications.

## Interaction conventions to preserve

- Compact native minimize/maximize/close controls, with Close at the maximized
  desktop's top-right corner.
- The charcoal/mint material theme, wordless peppermint sliding left sidebar and built-in
  searchable User manual with ten copyable example prompts.
- Saved conversations, visible pending actions, and user-editable task scope.
- Report observed outcomes plainly; keep unfinished work and uncertainty visible.
