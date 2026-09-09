# Peppermint

Peppermint is a local AI helper for the Linux Mint desktop. You give it an idea. It
does the work in the background and tells you the result.

Peppermint runs fully on your computer. It sends nothing to the internet.

```
peppermint "sort my Downloads folder by file type"
peppermint "make my desktop theme light"
peppermint "how much disk space is left?"
peppermint "make a shortcut so Ctrl+Alt+T opens the terminal"
```

## The panel icon

Peppermint puts an icon on the Cinnamon panel, next to the network, volume, and
battery buttons. It uses `XApp.StatusIcon`, the same interface that the Mint
update manager uses, so it matches the other indicators.

The icon tells you the state at a glance:

| Icon | Meaning |
| --- | --- |
| Gear | Peppermint waits for an idea |
| Turning arrows | Peppermint is working on a task |
| Warning triangle | Peppermint needs your approval or your answer |
| Error mark | The last task failed |

A **left click** opens the window. A **right click** opens a menu with the
tasks that wait for you, the recent tasks, and the state of the model.

The icon starts at login. To start it by hand:

```bash
peppermint-window --background     # only the panel icon
peppermint-window                  # the icon and the window
```

## How it works

```
[panel icon]    <-- D-Bus session bus -->  [peppermint-daemon]
[GTK 3 window]  <-- D-Bus session bus -->   |  task queue, agent loop,
[peppermint CLI]     <-- D-Bus session bus -->   |  storage, tools, notifications
                                            v
                                   [Ollama, localhost:11434]
```

The daemon does the work. It keeps running when you close the window. The
panel icon, the window, and the command line are three views of the same task
list.

## Tasks and diagnostics

The wordless **peppermint button** in the upper-left corner opens a sidebar that
slides in from the left: Tasks, Conversations, Diagnostics, User manual, New
conversation, Refresh and Hide window. Choosing a destination closes the sidebar
and opens that page. The current page appears beneath the window title. To close
the sidebar, click the peppermint button again, its close button, or the dimmed
area outside it, or press Escape.

The **Tasks** page shows saved work with global status counts, search, filters
and pagination. **Open** returns to the conversation; **Review** opens a waiting
question or approval. Checklist counts describe recorded steps, not a repair
guarantee or an estimate of time remaining. Switching views preserves drafts.

Open **Diagnostics → Start monitoring** for local CPU, memory, GPU, disk,
network and swap graphs. **Advanced** adds per-core usage, load, pressure,
temperatures, device rates and a searchable, sortable process table. Select a
process to inspect its latest readings. CPU 100% in that table means one logical
core; multi-core work can exceed 100%.

Monitoring starts off. Start authorizes read-only sampling every 2 or 5 seconds;
Pause stops it. Leaving Diagnostics or hiding the window pauses collection,
which resumes when you return if you left Start enabled. History stays in
memory, bounded to 150 readings, with 60-second and 5-minute chart windows.
Missing data and pauses leave gaps; rate measurements need two samples.
No model is invoked by the monitor. GPU readings currently support NVIDIA;
temperatures depend on available Linux sensors. Disk/network totals cover
physical devices/interfaces, and the process table contains up to 256 sampled
processes, ordered by CPU then memory. Search and sorting apply to that subset.

The **Diagnostics** button on a task adds its recorded activity times as context.
These observations do not establish which task caused a resource spike. Process
controls are available in the separate Recovery window described below; sending
a selected interval into a conversation remains future work. See the [task preview](docs/evaluations/peppermint-tasks.png)
and [diagnostics preview](docs/evaluations/peppermint-diagnostics-basic.png).

## Recovery and session controls

Press **Ctrl+Alt+Delete** to open **Peppermint Recovery** fullscreen above other
windows. The original Cinnamon logout shortcut moves to **Ctrl+Alt+Shift+Delete**.
You can also run `peppermint recover`. Recovery runs in a separate process and
does not need the main Peppermint window, daemon, or AI model to answer.

Search by process name or PID, select the exact process, and choose **Request
stop**. Read and confirm the selected process before continuing; unsaved work
may be lost. If that process remains running, **Force stop…** becomes available
for a separate confirmation. The action targets one process, not its entire
application family. **Refresh processes** updates the snapshot. Core display,
session and system processes are protected. A report that a process exited does
not establish that the underlying problem is fixed.

**Session controls** provide Lock screen, Switch user, Log out, Restart, Shut
down, and supported sleep modes. Log out, Restart and Shut down open Linux Mint's
normal confirmation flow, including applications that block the request. Lock,
Switch user and sleep actions ask for confirmation in Recovery. Switching users
locks the current session before opening the login screen. Unsupported sleep
modes are hidden; hibernate is currently unavailable on the development machine.
Recovery yields the screen for Mint's dialogs. A successful request is not a
report that logout, shutdown, or another session action has completed.

Use **Return to desktop** or **Escape** to close Recovery. Closing it cannot undo
a stop or session action already requested. This window can help when an
application or the main Peppermint window hangs. Cinnamon's keyboard service,
the display server, and the kernel must still respond; it cannot guarantee
screen takeover during a complete desktop or kernel freeze. Administrator access
does not remove that limit.

After the normal installation, configure Recovery from the project directory:

```bash
.venv/bin/python -m peppermint.recovery.shortcut --plan  # inspect planned shortcut changes
./scripts/install-recovery.sh                          # install shortcut and administrator helper
```

The installer checks all Cinnamon keyboard bindings before moving an occupied
Ctrl+Alt+Delete to an unused Ctrl+Alt+Shift+Delete. It refuses conflicting
assignments and preserves unrelated shortcuts. The shortcut launches the
installed checkout's absolute Python path, so restore and reinstall it if you
move the project.

Your own eligible processes can be stopped without elevation. Stopping a process
owned by another user requires the restricted helper at
`/usr/local/libexec/peppermint-recovery-helper` and Linux Mint's administrator
authentication. The installer copies that helper with root ownership; the
Recovery window itself runs as your normal user. Peppermint stores no password
and grants no permanent administrator session. **Check administrator access**
authenticates a read-only helper check without stopping a process. To install or
refresh just the helper, run `./scripts/install-recovery-admin.sh`.

The exact original shortcut values are saved in
`~/.local/share/peppermint/recovery-shortcut-backup.json` (under `$XDG_DATA_HOME`
when configured). To restore them:

```bash
.venv/bin/python -m peppermint.recovery.shortcut --restore
```

Restoration preserves later user changes and reports settings it could not
restore. It restores keyboard settings only; it does not remove the helper.

## Recurring tasks

Repeat an existing task using its original idea:

```bash
peppermint schedule add 3 "daily at 3pm"
peppermint schedule list
peppermint schedule pause 3
peppermint schedule resume 3
peppermint schedule remove 3
```

Supported phrases include `every Monday`, `every Friday at 09:30`, `daily`,
`every weekday`, `every weekend`, and `hourly`. Times are local; a day without a
specified time means midnight. A clock on the original task shows the schedule
and its paused/enabled state in a tooltip.

Each occurrence creates a new task with fresh history and normal approval
requests. An unfinished original task or previous occurrence blocks overlapping
runs, including while waiting for your approval or answer. Pausing affects future
occurrences; use the task's Stop control to cancel already queued work.

Peppermint prefers systemd user timers and writes `peppermint-task-<id>.timer`
and `.service` into `~/.config/systemd/user/`. If the user manager is unavailable,
it falls back to cron and warns you. Keep your desktop session running; missed
occurrences are not replayed after logout or shutdown. Removing a schedule
preserves task history. To change its timing, remove it and add it again.
See [recurring task implementation notes](docs/RECURRING_TASKS.md) for the API and
validation details.

## Safety

Every computer tool proposed in a conversation waits for your permission,
including read-only commands, file reads, desktop changes, and opening a browser. The window shows the exact
tool and arguments. **Allow once** executes that action; **Deny** leaves it unexecuted.
Asking you a question does not require permission, and selecting an approach
does not authorize its execution.

Open a task to see your full prompt, conversation, follow-ups, action status,
and expandable command output. New tasks open automatically. Conversations are
saved locally and remain available after restarting Peppermint. When there are
meaningfully different execution approaches, Peppermint can offer two or three
buttons, with a free-text answer available too.

Open the peppermint sidebar and choose **User manual** for getting started, task
and conversation controls, approvals and retests, diagnostics, recovery, session
controls, and ten example prompts. Search the manual for a topic and copy an example to
adapt to your own files. Reading the manual or copying an example does not start
a task.
See the [sidebar preview](docs/evaluations/peppermint-menu.png) and
[manual preview](docs/evaluations/peppermint-manual.png).

Minimize, maximize and close are tightly grouped at the upper-right edge. When
maximized, moving the pointer to the desktop's top-right corner lands on Close.
Closing hides the window while Peppermint continues running in the panel.

Peppermint never erases a file. A delete moves the file to the trash.

**An approval covers one exact action.** When you click Allow, Peppermint checks
three things before it acts: that the action is still the one you saw, that
your answer is less than 15 minutes old, and that the target on disk has not
changed since it asked. If a file became a link, or the arguments moved, Peppermint
refuses and tells you. Only the first click counts, so two windows cannot both
approve the same thing.

The backend records modification/change timestamps at the
filesystem's nanosecond precision. Older approvals for filesystem targets need
a fresh approval after that update; saved approval rows are not
rewritten. These metadata checks do not lock or hash file contents.

Approved reversible changes are recorded so you can put them back:

```bash
peppermint undo                      # list what Peppermint can put back
peppermint undo --apply --id 32      # put one change back
peppermint undo --task 15            # put back every change of one task
```

A change can only be put back once, and Peppermint refuses to overwrite anything
new that has appeared in the meantime.

## Install

You need Linux Mint with Cinnamon, and a GPU with 8 GB of memory or more for the
local AI model. The two setup scripts below install into your user account.
System packages and the separate recovery administrator helper require
administrator authentication. Recovery itself does not use the GPU or model.

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-notify-0.7 gir1.2-xapp-1.0   # usually already installed
./scripts/setup-ollama.sh      # installs Ollama in ~/.local and pulls the model
./scripts/install.sh           # installs Peppermint, the service, and the hotkey
```

Press **Super+Space** to open the window.

## Commands

| Command | What it does |
| --- | --- |
| `peppermint "an idea"` | Put an idea in the queue |
| `peppermint list` | Show the tasks |
| `peppermint show 3` | Show one task with its steps |
| `peppermint allow 3` / `peppermint deny 3` | Answer an approval request |
| `peppermint answer 3 "the blue one"` | Answer a question from Peppermint |
| `peppermint chat 3 "now do Documents too"` | Continue a finished task |
| `peppermint watch` | Follow the tasks live |
| `peppermint health` | Check the daemon and the model |
| `peppermint undo` | Show the old values Peppermint kept |
| `peppermint toggle` | Open or hide the window |
| `peppermint recover` | Open independent fullscreen process recovery and session controls |

## What Peppermint can do

All conversation computer tools require approval: bash commands, file operations, desktop
settings, package tools, scheduling, browser opening, notifications, and system
information. `ask_user` pauses for your answer without executing a computer action.
Internal planning and the `linux_reference` lookup use only the conversation or
bundled reference text, so they do not request computer-action approval.

## Linux, applications, and other operating systems

The support tools below build toward reliable Linux troubleshooting. Model
behavior still has documented limitations; see the
[current checkpoint](docs/PROJECT_STATUS.md).

Peppermint's new local reference library covers Steam/Proton, Wine,
Windows/Linux file sharing, NTFS and dual boot, paths and scripts across systems,
Flatpak, native packages, graphics, services, networking, and resource pressure.
It retrieves up to two relevant notes with sources and a review date. These are
troubleshooting references, not live measurements or a complete Linux manual.
Reading the bundled notes accesses no user files or network. References alone
cannot mark a repair plan step complete.

Plan steps now distinguish inspection, action, and verification. Completion
requires successful evidence from an allowed tool in the same task, and the UI
labels what that evidence establishes. A successful inspection or command does
not verify a repair. `request_retest` asks you to test a concrete symptom for an
unfinished verification step. **Passed**, **Still failing**, and **Not tested**
record your explicit report; only Passed completes that exact target, labelled
"User-reported pass." Ordinary chat, logs, and model claims cannot create this
evidence. A later computer change expires earlier reports for that conversation.
Retest reports and continuation state are saved together, so a restart can resume
without losing an accepted report. The CLI exposes the same choices with
`peppermint show TASK_ID` and `peppermint retest TASK_ID REQUEST_ID OUTCOME`.

Native text replies now require a separate structured dialogue decision before
finishing a task. Needed questions enter the normal waiting state, with saved
choices and reply history. Final answers retain their original wording. This
adds one local model call for a text reply; native tool questions already wait
directly. Invalid or cut-off decisions leave the task visibly unfinished.

For a request like “this game isn't working, fix it,” the intended workflow is:

1. Establish the game, launcher, and actual failure symptom.
2. With approval, identify its Steam installation and collect focused evidence.
3. Compare that evidence with relevant guidance before proposing a change.
4. Apply only approved changes, preserving saves and configuration.
5. Retest the original failure before saying it is fixed.

`steam_game_diagnostics` discovers native and Flatpak Steam libraries. With an
exact AppID, it inspects installation/prefix presence, storage and mount facts,
bounded log excerpts, and available host GPU/Vulkan probes. It records missing
or stale evidence explicitly and never launches, downloads, or repairs a game.
Probe availability describes whether a diagnostic command can be run; it does
not establish that the game's graphics runtime is healthy or broken.
Other app and cross-OS workflows use the reference lookup and existing approved
tools; Peppermint does not gain access to remote machines or accounts.

The model can still choose the wrong next step or misinterpret evidence; a
structured dialogue decision does not establish diagnostic correctness. No
actual game repair or general expert-level reliability is established by these
changes. See the [dialogue and retest evaluation](docs/evaluations/dialogue-2026-09-08.md),
[initial support evaluation](docs/evaluations/linux-support-2026-09-08.md)
and [reference sources](docs/research/linux-reference-sources-2026-09-08.md).

Run the focused model checks without executing its proposed actions:

```bash
.venv/bin/python scripts/evaluate_model.py --suite support --output /tmp/support.json
.venv/bin/python scripts/evaluate_model.py --suite support-reasoning --output /tmp/reasoning.json
.venv/bin/python scripts/evaluate_support_workflow.py --output /tmp/support-workflow.json
.venv/bin/python scripts/evaluate_dialogue.py --output /tmp/dialogue.json
```

The workflow command exercises the real agent using an in-memory task database
and simulated Steam diagnostics. Every other computer action is blocked.

## Hugging Face and future task-manager features

Compatible Hugging Face models can run through the local Ollama backend; model
cards help check architecture, templates, tool use and hardware requirements.
Public models can be researched without connecting an account. Private or gated
weights require the appropriate account access. See the
[model research and evaluation approach](docs/research/hugging-face-models-2026-09-08.md).

A pinned Qwen3.5-9B GGUF was downloaded, checksum-verified, imported locally,
and evaluated after fixing native tool-result messages and empty-reply recovery.
It still missed required clarification transitions, including with thinking
enabled. The default remains Qwen3:8b. See the
[comparison and protocol fixes](docs/evaluations/hf-protocol-2026-09-08.md).

The [roadmap](docs/ROADMAP.md) tracks the remaining diagnostics and recovery work.
Graphs and a process view are available in Diagnostics; the independent Recovery
window adds selected-process stopping and session controls. Stopping a Peppermint
conversation task does not mean a target application has exited. Using a new
model does not itself add screen access or train its weights.

## Opening web sites

```
peppermint "open x.com, Fidelity and YouTube"
```

Peppermint proposes each site in your normal browser and waits for approval
before opening it.

You can write the address the way you say it. `youtube.com` becomes
`https://youtube.com`. A full address works too.

Peppermint opens **only** `http` and `https`. It refuses `file:`, `javascript:`,
`data:`, and every other scheme, and it refuses a path on your computer. Those
are not web pages: `file:` reads your disk and `javascript:` runs code inside a
page you are already signed in to.

Peppermint never opens a web page with `run_shell`. The address is handed to
`xdg-open` as a single argument, so a semicolon or a backtick inside it stays
text and never becomes a command.

Signing in is separate and unchanged. Your browser still asks for your password
and your second factor, and Linux still asks for your password when something
needs root. Peppermint does not see, store, or type any of them.

## What to expect from a local model

Peppermint uses an 8 billion parameter model. It runs on your GPU and costs nothing,
but it is smaller than a cloud model. These weaknesses appeared in live tests:

**It loses one item in a long list.** When it moved ten files one by one, it
missed the ninth file every time and still reported success. This is why
`sort_folder` exists: one tool call sorts the whole folder in code, and the
tool reports exactly which files it did not place. Give Peppermint a job it can do
in few steps, not many.

**A text answer ends the turn.** Peppermint does not automatically ask the model
to continue after an answer. This prevents completed tasks from looping on
phrases such as "Let me know if you need anything else." Follow-up prompts are
saved and shown immediately when submitted. Responses have a 1,024-token limit
and model requests time out after 60 seconds without a response.

**It sometimes calls a tool that has nothing to do with your idea.** In one
test it changed the icon theme during a file-sorting task. The safety layer
limits the damage, because a settings change is recorded and easy to undo.

**It sometimes changes something you did not ask for.** In real use it
installed a package as asked and then changed the desktop wallpaper for no
reason. The change was recorded, so `peppermint undo` puts it back. Look at the
step list of a task before you trust the summary.

**It repeats a step.** It installed the same package twice in one task. This
wastes time but does no harm for an install. It is the reason a change now
records that it started, so a crash in the middle is never repeated blindly.

**Be exact.** "Tidy /home/me/Downloads by file type" works better than
"clean up my computer". A clear folder path and a clear goal give good results.

## Settings

Every parameter is in [peppermint/config.py](peppermint/config.py). An environment
variable with the `PEPPERMINT_` prefix overrides any of them:

```bash
PEPPERMINT_MODEL=spark PEPPERMINT_MODEL_SPARK=qwen2.5:1.5b-instruct systemctl --user restart peppermint-daemon
PEPPERMINT_MODEL=qwen2.5:7b-instruct-q4_K_M systemctl --user restart peppermint-daemon
```

| Name | Default | Meaning |
| --- | --- | --- |
| `PEPPERMINT_MODEL` | `spark` | The model Ollama serves (`spark` is default) |
| `PEPPERMINT_MODEL_SPARK` | `qwen2.5:1.5b-instruct` | Alias target when `PEPPERMINT_MODEL=spark` |
| `PEPPERMINT_MODEL_ASTRA` | `qwen3:8b` | Legacy alias target when `PEPPERMINT_MODEL=astra` |
| `PEPPERMINT_NUM_CTX` | `16384` | The context length. Lower it if the GPU fills. |
| `PEPPERMINT_TEMPERATURE` | `0.2` | Low, because Peppermint selects tools |
| `PEPPERMINT_MAX_ITERATIONS` | `30` | Tool calls per task |
| `PEPPERMINT_TASK_TIMEOUT_S` | `600` | Time limit per task |
| `PEPPERMINT_HOTKEY` | `<Super>space` | The key that opens the window |

## Files

```
peppermint/config.py            all parameters
peppermint/common/              data types, D-Bus interface
peppermint/daemon/agent.py      the agent loop
peppermint/daemon/safety.py     the risk rules
peppermint/daemon/db.py         SQLite storage
peppermint/daemon/main.py       the D-Bus service and the worker thread
peppermint/daemon/tools/        the tools the model can call
peppermint/ui/tray.py           the panel icon
peppermint/ui/                  the GTK 3 window
peppermint/recovery/            independent process recovery, session controls, shortcut setup
peppermint/cli.py               the command line
```

The task history lives in `~/.local/share/peppermint/peppermint.db`. The log is
`~/.local/share/peppermint/peppermint.log`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Publish to GitHub

The repository includes the CI workflow, issue forms, pull request template,
security policy, and a publishing helper. Install and authenticate the GitHub
CLI first:

```bash
gh auth login
./scripts/publish-github.sh
```

The helper creates the private `jescolmax/peppermint` repository, pushes the
current checkout, and creates a `Peppermint roadmap` Project board. Override
the defaults with `GITHUB_OWNER`, `GITHUB_REPOSITORY`, and
`GITHUB_PROJECT_TITLE`.

The tests need no GPU. A fake model drives the agent loop.

## Fix problems

```bash
systemctl --user status peppermint-daemon      # is the daemon running?
systemctl --user status ollama            # is the model server running?
peppermint health                              # what does Peppermint see?
journalctl --user -u peppermint-daemon -f      # follow the log
nvidia-smi                                # how full is the GPU?
```

If the GPU runs out of memory, lower the context length:

```bash
systemctl --user edit peppermint-daemon        # add Environment="PEPPERMINT_NUM_CTX=8192"
```

## Measuring model quality

The unit tests check application behavior; they do not measure the real model's
competence. Run the local evaluation without executing any proposed actions:

```bash
.venv/bin/python scripts/evaluate_model.py --repeats 2 --output /tmp/peppermint-eval.json
```

See the [capability assessment and improvement priorities](docs/evaluations/assessment-2026-09-07.md)
for measured results, limitations, and local model candidates. Long conversations
retain their full visible history, while model input keeps recent complete turns
within a bounded budget; older omitted details may need to be supplied again.

The [clarification follow-up](docs/evaluations/clarification-routing-2026-09-08.md)
records targeted checks for vague cleanup requests, impossible hardware claims,
and waiting for answers, including the remaining failures.

## Video playback alongside AI

Ask Peppermint to show solutions for two video windows and one or two AI prompts.
It requests permission for a short performance sample, then shows measured
headroom, local and browser-AI options, tradeoffs and a verification procedure.
A completed report does not mean the simultaneous playback workload has been
validated. Peppermint currently queues its own model tasks; enabling parallel
requests in Ollama alone does not change the app's worker queue.

Complex tasks can track a persistent plan. Stop cancels model generation, and
failed commands remain visible as failures. Read the [implementation and validation
notes](docs/evaluations/video-ai-upgrade.md) and see the [window preview](docs/evaluations/peppermint-material-ui.png).

The interface uses graphite and mint with square controls, dark outlines, and
light edge highlights. Bundled earth, bone, wood, and brushed-metal textures cover
the background, panels, messages, and controls. [Permission prompts](docs/evaluations/peppermint-material-approval.png)
use a pale surface to stand out. Textures run entirely locally.

To preview the actual GTK interface with sample conversations and no daemon or
computer actions, run:

```bash
.venv/bin/python scripts/preview_ui.py --scene conversation --output /tmp/peppermint-preview.png
```

Other scenes are `empty`, `approval`, `question`, `working`, `retest`, `tasks`,
`basic`, `advanced` and `manual`. Diagnostics previews use illustrative fixtures
and never start monitoring. Add `--menu` to show the peppermint sidebar in a preview.
