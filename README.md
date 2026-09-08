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

## Safety

Every computer tool waits for your permission, including read-only commands,
file reads, desktop changes, and opening a browser. The window shows the exact
tool and arguments. **Allow once** executes that action; **Deny** leaves it unexecuted.
Asking you a question does not require permission, and selecting an approach
does not authorize its execution.

Open a task to see your full prompt, conversation, follow-ups, action status,
and expandable command output. New tasks open automatically. Conversations are
saved locally and remain available after restarting Peppermint. When there are
meaningfully different execution approaches, Peppermint can offer two or three
buttons, with a free-text answer available too.

Peppermint never erases a file. A delete moves the file to the trash.

**An approval covers one exact action.** When you click Allow, Peppermint checks
three things before it acts: that the action is still the one you saw, that
your answer is less than 15 minutes old, and that the target on disk has not
changed since it asked. If a file became a link, or the arguments moved, Peppermint
refuses and tells you. Only the first click counts, so two windows cannot both
approve the same thing.

Approved reversible changes are recorded so you can put them back:

```bash
peppermint undo                      # list what Peppermint can put back
peppermint undo --apply --id 32      # put one change back
peppermint undo --task 15            # put back every change of one task
```

A change can only be put back once, and Peppermint refuses to overwrite anything
new that has appeared in the meantime.

## Install

You need Linux Mint with Cinnamon, and a GPU with 8 GB of memory or more.
Neither script needs root.

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-notify-0.7 gir1.2-xapp-1.0   # usually already installed
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

## What Peppermint can do

All computer tools require approval: bash commands, file operations, desktop
settings, package tools, scheduling, browser opening, notifications, and system
information. `ask_user` pauses for your answer without executing a computer action.

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
PEPPERMINT_MODEL=qwen2.5:7b-instruct-q4_K_M systemctl --user restart peppermint-daemon
```

| Name | Default | Meaning |
| --- | --- | --- |
| `PEPPERMINT_MODEL` | `qwen3:8b` | The model Ollama serves |
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

## Video playback alongside AI

Ask Peppermint to show solutions for two video windows and one or two AI prompts.
It requests permission for a short performance sample, then shows measured
headroom, local and browser-AI options, tradeoffs and a verification procedure.
A completed report does not mean the simultaneous playback workload has been
validated. Peppermint currently queues its own model tasks; enabling parallel
requests in Ollama alone does not change the app's worker queue.

Complex tasks can track a persistent plan. Stop cancels model generation, and
failed commands remain visible as failures. Read the [implementation and validation
notes](docs/evaluations/video-ai-upgrade.md) and see the [window preview](docs/evaluations/peppermint-ui.png).
