"""The Peppermint command line.

    peppermint "sort my Downloads folder"     put an idea in the queue
    peppermint list                           show the tasks
    peppermint show 3                         show one task with its steps
    peppermint allow 3 / peppermint deny 3         answer an approval request
    peppermint answer 3 "the blue one"        answer a question
    peppermint retest 3 REQUEST_ID passed     report a requested retest outcome
    peppermint chat 3 "now do the same for Documents"
    peppermint watch                          follow the tasks live
    peppermint health                         check the daemon and the model
    peppermint export 3 / peppermint export --all
    peppermint import tasks.peppermint         restore task history with new IDs
    peppermint schedule add 3 "daily at 3pm"  repeat a task in local time
    peppermint schedule list | pause 3 | resume 3 | remove 3
    peppermint toggle                         open or hide the window
    peppermint plugin list | enable <name> | disable <name>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from peppermint.common import dbus_api  # noqa: E402
from peppermint.common.models import Status  # noqa: E402

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
        print("Give an idea, for example: peppermint \"make my theme light\"", file=sys.stderr)
        return 2
    result = dbus_api.call_daemon("AddTask", GLib.Variant("(s)", (idea,)), GLib.VariantType("(i)"))
    task_id = result.unpack()[0]
    print(f"Task {task_id} is in the queue. Watch it with: peppermint show {task_id}")
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
        print(f"\n{BOLD}Peppermint asks for approval:{RESET} {task['pending']['description']}")
        print(f"Allow it with:  peppermint allow {task['id']}")
        print(f"Refuse it with: peppermint deny {task['id']}")
    if task["status"] == Status.AWAITING_INPUT.value and task.get("retest"):
        retest = task["retest"]
        print(f"\n{BOLD}Peppermint asks you to retest:{RESET} {retest['question']}")
        print(f"Original symptom: {retest['symptom']}")
        print(f"Plan step: {retest['target_description']}")
        print("Report what you observed:")
        for outcome in dbus_api.RETEST_OUTCOMES:
            print(f"  peppermint retest {task['id']} {retest['request_id']} {outcome}")
    elif task["status"] == Status.AWAITING_INPUT.value and task.get("question"):
        print(f"\n{BOLD}Peppermint asks:{RESET} {task['question']}")
        print(f"Answer with: peppermint answer {task['id']} \"your answer\"")
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


def cmd_retest(args) -> int:
    dbus_api.call_daemon("Retest", GLib.Variant("(iss)", (args.id, args.request_id, args.outcome)))
    print(f"Task {args.id}: retest outcome submitted ({args.outcome}).")
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


def cmd_export(args) -> int:
    """Print the path of the archive created by the daemon."""
    method = "ExportAll" if args.all else "ExportTask"
    params = None if args.all else GLib.Variant("(i)", (args.task_id,))
    result = dbus_api.call_daemon(method, params, GLib.VariantType("(s)"))
    print(result.unpack()[0])
    return 0


def cmd_fork(args) -> int:
    result = dbus_api.call_daemon(
        "ForkTask",
        GLib.Variant("(iis)", (args.task_id, args.at, " ".join(args.idea))),
        GLib.VariantType("(i)"),
    )
    task_id = result.unpack()[0]
    print(f"Forked task {args.task_id} into {task_id}.")
    return 0


def cmd_import(args) -> int:
    """Resolve the caller's path before sending it to the background daemon."""
    try:
        path = Path(args.file).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("The archive path must point to a file.")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Peppermint cannot open the archive: {exc}", file=sys.stderr)
        return 1
    result = dbus_api.call_daemon(
        "ImportArchive", GLib.Variant("(s)", (str(path),)), GLib.VariantType("(s)"),
    )
    try:
        outcome = json.loads(result.unpack()[0])
        task_ids, warnings = outcome["task_ids"], outcome["warnings"]
        if (not isinstance(task_ids, list) or not isinstance(warnings, list)
                or any(type(task_id) is not int or task_id <= 0 for task_id in task_ids)
                or any(not isinstance(warning, str) for warning in warnings)):
            raise ValueError("Invalid import result")
    except (ValueError, TypeError, KeyError) as exc:
        print("Peppermint received an invalid import result. Check the task list before retrying.",
              file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if task_ids:
        print("Imported tasks: " + ", ".join(str(task_id) for task_id in task_ids))
    else:
        print("The archive contained no tasks.")
    return 0


def cmd_plugin(args) -> int:
    if args.action == "list":
        response = dbus_api.call_daemon("ListPlugins", None, GLib.VariantType("(s)"))
        plugins = json.loads(response.unpack()[0])
        if not plugins:
            print("No plugins found in ~/.config/peppermint/tools.")
            return 0
        for plugin in plugins:
            state = "enabled" if plugin["enabled"] else "disabled"
            tools = ", ".join(plugin["tools"]) if plugin["tools"] else "-"
            print(f"{plugin['name']}: {state} ({tools})")
        return 0

    method = "EnablePlugin" if args.action == "enable" else "DisablePlugin"
    dbus_api.call_daemon(method, GLib.Variant("(s)", (args.name,)))
    print(f"Plugin '{args.name}' {args.action}d.")
    return 0


def _positive_task_id(value: str) -> int:
    try:
        task_id = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("task ID must be a positive integer") from None
    if not 1 <= task_id <= 2_147_483_647:
        raise argparse.ArgumentTypeError("task ID must be between 1 and 2147483647")
    return task_id


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
        print("Peppermint has changed nothing that it can put back.")
        return 0
    print("Peppermint can put these changes back:\n")
    for row in rows:
        print(f" [{row['id']}] task {row['task_id']}: {undo_line(row)}")
    print("\nPut one back with:      peppermint undo --apply --id <number>")
    print("Put a whole task back:  peppermint undo --task <task number>")
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
            print(f"    run: peppermint show {task_id}")

    bus.signal_subscribe(None, dbus_api.DAEMON_IFACE, "TaskUpdated", dbus_api.DAEMON_PATH,
                         None, 0, on_signal)
    print("Peppermint watches the tasks. Press Ctrl+C to stop.")
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_toggle(args) -> int:
    if not dbus_api.name_has_owner(dbus_api.WINDOW_NAME):
        import subprocess
        subprocess.Popen([sys.executable, "-m", "peppermint.ui.app"],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0
    dbus_api.call_window("Toggle")
    return 0


def cmd_recover(args) -> int:
    """Launch recovery independently from the daemon and normal window."""
    import subprocess
    subprocess.Popen([sys.executable, '-m', 'peppermint.recovery.app'],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def cmd_schedule(args) -> int:
    if args.action == "list":
        result = dbus_api.call_daemon("ListSchedules", reply_type=GLib.VariantType("(s)"))
        schedules = json.loads(result.unpack()[0])
        if not schedules:
            print("No recurring tasks.")
        for row in schedules:
            state = "enabled" if row["enabled"] else "paused"
            print(f"[{row['task_id']}] {row['schedule_text']} · {state} · {row['backend']}")
        return 0
    if args.action == "add":
        result = dbus_api.call_daemon("CreateSchedule", GLib.Variant("(is)", (args.task_id, args.schedule)),
                                     GLib.VariantType("(s)"))
        row = json.loads(result.unpack()[0])
        print(f"Task {args.task_id}: {row['schedule_text']} ({row['backend']}, local time).")
        if row.get("warning"):
            print("Warning: " + row["warning"], file=sys.stderr)
        return 0
    method = {"pause": "PauseSchedule", "resume": "ResumeSchedule", "remove": "RemoveSchedule"}[args.action]
    dbus_api.call_daemon(method, GLib.Variant("(i)", (args.task_id,)))
    print(f"Task {args.task_id}: schedule { {'pause': 'paused', 'resume': 'resumed', 'remove': 'removed'}[args.action]}.")
    return 0


def cmd_run_scheduled(args) -> int:
    result = dbus_api.call_daemon("RunScheduled", GLib.Variant("(i)", (args.task_id,)), GLib.VariantType("(i)"))
    run_id = result.unpack()[0]
    print(f"Queued scheduled task {run_id}." if run_id else "Skipped: schedule inactive or a previous task is unfinished.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peppermint", description="Peppermint, your helper on Linux Mint.")
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

    p = subs.add_parser("allow", help="allow the action Peppermint asked about")
    p.add_argument("id", type=int)
    p.set_defaults(func=lambda a: cmd_confirm(a, True))

    p = subs.add_parser("deny", help="refuse the action Peppermint asked about")
    p.add_argument("id", type=int)
    p.set_defaults(func=lambda a: cmd_confirm(a, False))

    p = subs.add_parser("answer", help="answer a question from Peppermint")
    p.add_argument("id", type=int)
    p.add_argument("text", nargs="+")
    p.set_defaults(func=cmd_answer)

    p = subs.add_parser("retest", help="report the result of a requested retest")
    p.add_argument("id", type=int)
    p.add_argument("request_id", help="request ID shown by peppermint show")
    p.add_argument("outcome", choices=dbus_api.RETEST_OUTCOMES)
    p.set_defaults(func=cmd_retest)

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

    p = subs.add_parser("export", help="save a task or all tasks in a .peppermint ZIP archive")
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument("task_id", nargs="?", type=_positive_task_id, help="task ID to export")
    selection.add_argument("--all", action="store_true", help="export every task")
    p.set_defaults(func=cmd_export)

    p = subs.add_parser("import", help="restore a .peppermint archive with new task IDs")
    p.add_argument("file", help="path to the .peppermint archive")
    p.set_defaults(func=cmd_import)

    p = subs.add_parser("fork", help="fork a task from one historical step")
    p.add_argument("task_id", type=_positive_task_id, help="existing task ID")
    p.add_argument("--at", type=int, required=True, help="0-based step index to copy through")
    p.add_argument("idea", nargs="+")
    p.set_defaults(func=cmd_fork)

    p = subs.add_parser("schedule", help="manage recurring tasks (local time)")
    actions = p.add_subparsers(dest="action", required=True)
    for action in ("list", "add", "pause", "resume", "remove"):
        sub = actions.add_parser(action)
        if action != "list":
            sub.add_argument("task_id", type=_positive_task_id)
        if action == "add":
            sub.add_argument("schedule", help='for example "every Monday" or "daily at 3pm"')
        sub.set_defaults(func=cmd_schedule)

    p = subs.add_parser("run-scheduled", help="enqueue one occurrence of an enabled schedule")
    p.add_argument("task_id", type=_positive_task_id)
    p.set_defaults(func=cmd_run_scheduled)

    p = subs.add_parser("plugin", help="show, enable, and disable plugins")
    plugin_actions = p.add_subparsers(dest="action", required=True)

    list_plugins = plugin_actions.add_parser("list", help="show available plugins")
    list_plugins.set_defaults(func=cmd_plugin, action="list")

    enable_plugin = plugin_actions.add_parser("enable", help="enable a plugin")
    enable_plugin.add_argument("name", help="plugin name")
    enable_plugin.set_defaults(func=cmd_plugin, action="enable")

    disable_plugin = plugin_actions.add_parser("disable", help="disable a plugin")
    disable_plugin.add_argument("name", help="plugin name")
    disable_plugin.set_defaults(func=cmd_plugin, action="disable")

    p = subs.add_parser("undo", help="list or put back the changes Peppermint made")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--apply", action="store_true", help="put the change back")
    p.add_argument("--id", type=int, default=0, help="which recorded change")
    p.add_argument("--task", type=int, default=0, help="put back every change of one task")
    p.set_defaults(func=cmd_undo)

    p = subs.add_parser("toggle", help="open or hide the Peppermint window")
    p.set_defaults(func=cmd_toggle)

    p = subs.add_parser("recover", help="open the independent recovery window")
    p.set_defaults(func=cmd_recover)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # `peppermint "an idea"` is the same as `peppermint add "an idea"`.
    known = {"add", "list", "show", "allow", "deny", "answer", "retest", "chat",
             "cancel", "watch", "health", "export", "import", "fork", "undo",
             "toggle", "recover", "plugin", "schedule", "run-scheduled", "-h", "--help"}
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
        print(f"Peppermint hit a bus error: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
