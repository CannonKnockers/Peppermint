# Minty

Minty is a local AI helper for the Linux Mint desktop. You give it an idea. It
does the work in the background and tells you the result.

Minty runs fully on your computer. It sends nothing to the internet.

```
minty "sort my Downloads folder by file type"
minty "make my desktop theme light"
minty "how much disk space is left?"
minty "make a shortcut so Ctrl+Alt+T opens the terminal"
```

## The panel icon

Minty puts an icon on the Cinnamon panel, next to the network, volume, and
battery buttons. It uses `XApp.StatusIcon`, the same interface that the Mint
update manager uses, so it matches the other indicators.

The icon tells you the state at a glance:

| Icon | Meaning |
| --- | --- |
| Gear | Minty waits for an idea |
| Turning arrows | Minty is working on a task |
| Warning triangle | Minty needs your approval or your answer |
| Error mark | The last task failed |

A **left click** opens the window. A **right click** opens a menu with the
tasks that wait for you, the recent tasks, and the state of the model.

The icon starts at login. To start it by hand:

```bash
minty-window --background     # only the panel icon
minty-window                  # the icon and the window
```

## How it works

```
[panel icon]    <-- D-Bus session bus -->  [minty-daemon]
[GTK 3 window]  <-- D-Bus session bus -->   |  task queue, agent loop,
[minty CLI]     <-- D-Bus session bus -->   |  storage, tools, notifications
                                            v
                                   [Ollama, localhost:11434]
```

The daemon does the work. It keeps running when you close the window. The
panel icon, the window, and the command line are three views of the same task
list.

## Safety

Minty divides every action into two classes.

**Safe actions run at once.** These only read, or they are easy to undo:
list a folder, read a file, read a setting, search for a package, change a
desktop theme.

**Risky actions wait for you.** Minty shows you the exact action and two
buttons. Nothing happens until you click Allow. These include: any command
that is not on the read-only list, a delete, a write that replaces a file, a
package install, and a scheduled job.

The rule is strict: if the action is not clearly safe, Minty asks. The tool
layer decides this. The model never decides its own risk class. See
[minty/daemon/safety.py](minty/daemon/safety.py).

Minty never erases a file. A delete moves the file to the trash.

**An approval covers one exact action.** When you click Allow, Minty checks
three things before it acts: that the action is still the one you saw, that
your answer is less than 15 minutes old, and that the target on disk has not
changed since it asked. If a file became a link, or the arguments moved, Minty
refuses and tells you. Only the first click counts, so two windows cannot both
approve the same thing.

**Reversible is not the same as harmless.** Minty applies a desktop setting
without asking because it can put it back, not because the change does not
matter. Every such change is recorded:

```bash
minty undo                      # list what Minty can put back
minty undo --apply --id 32      # put one change back
minty undo --task 15            # put back every change of one task
```

A change can only be put back once, and Minty refuses to overwrite anything
new that has appeared in the meantime.

## Install

You need Linux Mint with Cinnamon, and a GPU with 8 GB of memory or more.
Neither script needs root.

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-notify-0.7 gir1.2-xapp-1.0   # usually already installed
./scripts/setup-ollama.sh      # installs Ollama in ~/.local and pulls the model
./scripts/install.sh           # installs Minty, the service, and the hotkey
```

Press **Super+Space** to open the window.

## Commands

| Command | What it does |
| --- | --- |
| `minty "an idea"` | Put an idea in the queue |
| `minty list` | Show the tasks |
| `minty show 3` | Show one task with its steps |
| `minty allow 3` / `minty deny 3` | Answer an approval request |
| `minty answer 3 "the blue one"` | Answer a question from Minty |
| `minty chat 3 "now do Documents too"` | Continue a finished task |
| `minty watch` | Follow the tasks live |
| `minty health` | Check the daemon and the model |
| `minty undo` | Show the old values Minty kept |
| `minty toggle` | Open or hide the window |

## What Minty can do

| Tool | Risk |
| --- | --- |
| `run_shell` | Read-only commands are safe. Everything else needs approval. |
| `read_file`, `list_dir`, `search_files` | Safe |
| `sort_folder` | Sorts a whole folder into subfolders in one step. Safe inside your home. |
| `write_file` | Safe for a new file in your home. A replacement needs approval. |
| `move_file`, `make_dir` | Safe inside your home |
| `delete_file` | Needs approval. Moves to the trash. |
| `gsettings_get`, `gsettings_list`, `list_themes` | Safe |
| `gsettings_set` | Safe for desktop settings. Minty keeps the old value. |
| `set_keybinding`, `list_keybindings` | A new shortcut needs approval |
| `apt_query`, `list_apps` | Safe |
| `apt_install` | Needs approval. Asks for your password. |
| `schedule`, `unschedule`, `list_scheduled` | Needs approval |
| `open_url` | Safe. Opens a web page at once, without asking. |
| `system_info`, `notify_user` | Safe |
| `ask_user` | Stops the task until you answer |

## Opening web sites

```
minty "open x.com, Fidelity and YouTube"
```

Minty opens each site in your normal browser at once. It does not ask for
approval, because showing you a page changes nothing on the computer: nothing is
written, nothing is installed, and you can close the tab.

You can write the address the way you say it. `youtube.com` becomes
`https://youtube.com`. A full address works too.

Minty opens **only** `http` and `https`. It refuses `file:`, `javascript:`,
`data:`, and every other scheme, and it refuses a path on your computer. Those
are not web pages: `file:` reads your disk and `javascript:` runs code inside a
page you are already signed in to.

Minty never opens a web page with `run_shell`. The address is handed to
`xdg-open` as a single argument, so a semicolon or a backtick inside it stays
text and never becomes a command.

Signing in is separate and unchanged. Your browser still asks for your password
and your second factor, and Linux still asks for your password when something
needs root. Minty does not see, store, or type any of them.

## What to expect from a local model

Minty uses an 8 billion parameter model. It runs on your GPU and costs nothing,
but it is smaller than a cloud model. These weaknesses appeared in live tests:

**It loses one item in a long list.** When it moved ten files one by one, it
missed the ninth file every time and still reported success. This is why
`sort_folder` exists: one tool call sorts the whole folder in code, and the
tool reports exactly which files it did not place. Give Minty a job it can do
in few steps, not many.

**It stops early and describes work it has not done.** Minty detects text like
"Now I will create the folders" and pushes the model to continue. See
`promises_more` in [minty/daemon/agent.py](minty/daemon/agent.py).

**It sometimes calls a tool that has nothing to do with your idea.** In one
test it changed the icon theme during a file-sorting task. The safety layer
limits the damage, because a settings change is recorded and easy to undo.

**It sometimes changes something you did not ask for.** In real use it
installed a package as asked and then changed the desktop wallpaper for no
reason. The change was recorded, so `minty undo` puts it back. Look at the
step list of a task before you trust the summary.

**It repeats a step.** It installed the same package twice in one task. This
wastes time but does no harm for an install. It is the reason a change now
records that it started, so a crash in the middle is never repeated blindly.

**Be exact.** "Tidy /home/me/Downloads by file type" works better than
"clean up my computer". A clear folder path and a clear goal give good results.

## Settings

Every parameter is in [minty/config.py](minty/config.py). An environment
variable with the `MINTY_` prefix overrides any of them:

```bash
MINTY_MODEL=qwen2.5:7b-instruct-q4_K_M systemctl --user restart minty-daemon
```

| Name | Default | Meaning |
| --- | --- | --- |
| `MINTY_MODEL` | `qwen3:8b` | The model Ollama serves |
| `MINTY_NUM_CTX` | `16384` | The context length. Lower it if the GPU fills. |
| `MINTY_TEMPERATURE` | `0.2` | Low, because Minty selects tools |
| `MINTY_MAX_ITERATIONS` | `30` | Tool calls per task |
| `MINTY_TASK_TIMEOUT_S` | `600` | Time limit per task |
| `MINTY_HOTKEY` | `<Super>space` | The key that opens the window |

## Files

```
minty/config.py            all parameters
minty/common/              data types, D-Bus interface
minty/daemon/agent.py      the agent loop
minty/daemon/safety.py     the risk rules
minty/daemon/db.py         SQLite storage
minty/daemon/main.py       the D-Bus service and the worker thread
minty/daemon/tools/        the tools the model can call
minty/ui/tray.py           the panel icon
minty/ui/                  the GTK 3 window
minty/cli.py               the command line
```

The task history lives in `~/.local/share/minty/minty.db`. The log is
`~/.local/share/minty/minty.log`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The tests need no GPU. A fake model drives the agent loop.

## Fix problems

```bash
systemctl --user status minty-daemon      # is the daemon running?
systemctl --user status ollama            # is the model server running?
minty health                              # what does Minty see?
journalctl --user -u minty-daemon -f      # follow the log
nvidia-smi                                # how full is the GPU?
```

If the GPU runs out of memory, lower the context length:

```bash
systemctl --user edit minty-daemon        # add Environment="MINTY_NUM_CTX=8192"
```
