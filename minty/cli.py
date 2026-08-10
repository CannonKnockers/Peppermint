"""The Minty command line.

    minty "sort my Downloads folder"     put an idea in the queue
    minty list                           show the tasks
    minty show 3                         show one task with its steps
    minty allow 3 / minty deny 3         answer an approval request
    minty answer 3 "the blue one"        answer a question
    minty chat 3 "now do the same for Documents"
    minty watch                          follow the tasks live
    minty health                         check the daemon and the model
    minty toggle                         open or hide the window
"""

from __future__ import annotations

import argparse
import json
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from minty.common import dbus_api  # noqa: E402
from minty.common.models import Status  # noqa: E402

COLORS = {
    "queued": "\033[90m", "planning": "\033[90m", "running": "\033[94m",
    "awaiting-confirmation": "\033[93m", "awaiting-input": "\033[93m",
    "done": "\033[92m", "failed": "\033[91m", "cancelled": "\033[90m",
}
RESET = "\033[0m"
BOLD = "\033[1m"


def paint(status: str) -> str:
    if not sys.stdout.isatty():
        return status
    return f"{COLORS.get(status, '')}{status}{RESET}"


def cmd_add(args) -> int:
    idea = " ".join(args.idea).strip()
    if not idea:
        print("Give an idea, for example: minty \"make my theme light\"", file=sys.stderr)
        return 2
    result = dbus_api.call_daemon("AddTask", GLib.Variant("(s)", (idea,)), GLib.VariantType("(i)"))
    task_id = result.unpack()[0]
    print(f"Task {task_id} is in the queue. Watch it with: minty show {task_id}")
    return 0


def cmd_list(args) -> int:
    result = dbus_api.call_daemon("ListTasks", GLib.Variant("(i)", (args.limit,)), GLib.VariantType("(s)"))
    tasks = json.loads(result.unpack()[0])
    if not tasks:
        print("There is no task yet.")
        return 0
    for task in tasks:
        idea = task["idea"].replace("\n", " ")
        if len(idea) > 58:
            idea = idea[:55] + "..."
        print(f"{task['id']:>4}  {paint(task['status']):<32}  {idea}")
    return 0


def cmd_show(args) -> int:
    result = dbus_api.call_daemon("GetTask", GLib.Variant("(i)", (args.id,)), GLib.VariantType("(s)"))
    task = json.loads(result.unpack()[0])
    if task is None:
        print(f"There is no task {args.id}.", file=sys.stderr)
        return 1

    print(f"{BOLD}Task {task['id']}{RESET}  [{paint(task['status'])}]")
    print(f"Idea: {task['idea']}")
    print()
    for step in task["steps"]:
        mark = {"ok": "+", "error": "!", "pending": "?", "denied": "x", "asked": "?"}.get(step["status"], "-")
        args_text = json.dumps(step["args"])
        if len(args_text) > 90:
            args_text = args_text[:87] + "..."
        print(f" {mark} {step['tool']} {args_text}")
        output = (step["output"] or "").strip()
        if output:
            for line in output.splitlines()[:6]:
                print(f"     {line[:110]}")
            extra = len(output.splitlines()) - 6
            if extra > 0:
                print(f"     ... {extra} more lines")

    if task.get("pending"):
        print(f"\n{BOLD}Minty asks for approval:{RESET} {task['pending']['description']}")
        print(f"Allow it with:  minty allow {task['id']}")
        print(f"Refuse it with: minty deny {task['id']}")
    if task["status"] == Status.AWAITING_INPUT.value and task.get("question"):
        print(f"\n{BOLD}Minty asks:{RESET} {task['question']}")
        print(f"Answer with: minty answer {task['id']} \"your answer\"")
    if task["result"]:
        print(f"\n{BOLD}Result:{RESET}\n{task['result']}")
    if task["error"]:
        print(f"\n{BOLD}Error:{RESET} {task['error']}")
    return 0


def cmd_confirm(args, approved: bool) -> int:
    dbus_api.call_daemon("Confirm", GLib.Variant("(ib)", (args.id, approved)))
    print(f"Task {args.id}: you {'allowed' if approved else 'refused'} the action.")
    return 0


def cmd_answer(args) -> int:
    dbus_api.call_daemon("Answer", GLib.Variant("(is)", (args.id, " ".join(args.text))))
    print(f"Task {args.id} continues.")
    return 0


def cmd_chat(args) -> int:
    dbus_api.call_daemon("Chat", GLib.Variant("(is)", (args.id, " ".join(args.text))))
    print(f"Task {args.id} continues with your message.")
    return 0


def cmd_cancel(args) -> int:
    dbus_api.call_daemon("Cancel", GLib.Variant("(i)", (args.id,)))
    print(f"Task {args.id} stops.")
    return 0


def cmd_health(args) -> int:
    result = dbus_api.call_daemon("Health", None, GLib.VariantType("(s)"))
    data = json.loads(result.unpack()[0])
    print(f"daemon:  running")
    print(f"model:   {data['model']}")
    print(f"ollama:  {'answers' if data['ok'] else 'does not answer'}")
    print(f"models:  {data['models']}")
    print(f"queue:   {data['queue']} jobs waiting")
    print(f"data:    {data['db']}")
    return 0 if data["ok"] else 1


def cmd_undo(args) -> int:
    if args.apply or args.task:
        result = dbus_api.call_daemon(
            "Revert", GLib.Variant("(ii)", (args.id or 0, args.task or 0)),
            GLib.VariantType("(s)"), timeout=60000)
        outcome = json.loads(result.unpack()[0])
        if not outcome:
            print("There is nothing to put back.")
            return 0
        failures = 0
        for row in outcome:
            mark = "+" if row["ok"] else "!"
            failures += not row["ok"]
            print(f" {mark} {row['message']}")
        return 1 if failures else 0

    result = dbus_api.call_daemon("Undo", GLib.Variant("(i)", (args.limit,)), GLib.VariantType("(s)"))
    rows = json.loads(result.unpack()[0])
    if not rows:
        print("Minty has changed nothing that it can put back.")
        return 0
    print("Minty can put these changes back:\n")
    for row in rows:
        print(f" [{row['id']}] task {row['task_id']}: {undo_line(row)}")
    print("\nPut one back with:      minty undo --apply --id <number>")
    print("Put a whole task back:  minty undo --task <task number>")
    return 0


def undo_line(row: dict) -> str:
    kind, target, old = row["kind"], row["target"], row["old_value"]
    if kind == "gsettings":
        return f"set {target} back to {old[:60]}"
    if kind == "move":
        return f"move {target} back to {old}"
    if kind == "file":
        return f"restore the earlier contents of {target}"
    return f"{kind} on {target}"


def cmd_watch(args) -> int:
    loop = GLib.MainLoop()
    bus = dbus_api.session_bus()

    def on_signal(_conn, _sender, _path, _iface, _signal, params):
        task_id, status = params.unpack()
        print(f"task {task_id}: {paint(status)}")
        if status in ("awaiting-confirmation", "awaiting-input"):
            print(f"    run: minty show {task_id}")

    bus.signal_subscribe(None, dbus_api.DAEMON_IFACE, "TaskUpdated", dbus_api.DAEMON_PATH,
                         None, 0, on_signal)
    print("Minty watches the tasks. Press Ctrl+C to stop.")
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_toggle(args) -> int:
    if not dbus_api.name_has_owner(dbus_api.WINDOW_NAME):
        import subprocess
        subprocess.Popen([sys.executable, "-m", "minty.ui.app"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0
    dbus_api.call_window("Toggle")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minty", description="Minty, your helper on Linux Mint.")
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("add", help="put an idea in the queue")
    p.add_argument("idea", nargs="+")
    p.set_defaults(func=cmd_add)

    p = subs.add_parser("list", help="show the tasks")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = subs.add_parser("show", help="show one task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = subs.add_parser("allow", help="allow the action Minty asked about")
    p.add_argument("id", type=int)
    p.set_defaults(func=lambda a: cmd_confirm(a, True))

    p = subs.add_parser("deny", help="refuse the action Minty asked about")
    p.add_argument("id", type=int)
    p.set_defaults(func=lambda a: cmd_confirm(a, False))

    p = subs.add_parser("answer", help="answer a question from Minty")
    p.add_argument("id", type=int)
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_answer)

    p = subs.add_parser("chat", help="send a follow-up message")
    p.add_argument("id", type=int)
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_chat)

    p = subs.add_parser("cancel", help="stop a task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_cancel)

    p = subs.add_parser("watch", help="follow the tasks live")
    p.set_defaults(func=cmd_watch)

    p = subs.add_parser("health", help="check the daemon and the model")
    p.set_defaults(func=cmd_health)

    p = subs.add_parser("undo", help="list or put back the changes Minty made")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--apply", action="store_true", help="put the change back")
    p.add_argument("--id", type=int, default=0, help="which recorded change")
    p.add_argument("--task", type=int, default=0, help="put back every change of one task")
    p.set_defaults(func=cmd_undo)

    p = subs.add_parser("toggle", help="open or hide the Minty window")
    p.set_defaults(func=cmd_toggle)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `minty "an idea"` is the same as `minty add "an idea"`.
    known = {"add", "list", "show", "allow", "deny", "answer", "chat",
             "cancel", "watch", "health", "undo", "toggle", "-h", "--help"}
    if argv and argv[0] not in known:
        argv.insert(0, "add")

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except dbus_api.DaemonNotRunning as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except GLib.Error as exc:
        print(f"Minty hit a bus error: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
