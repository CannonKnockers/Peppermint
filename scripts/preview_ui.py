#!/usr/bin/env python3
r"""Capture the actual GTK window with isolated, illustrative conversation data.

Run from any directory using a Python with GTK 3/PyGObject available::

    .venv/bin/python scripts/preview_ui.py --scene conversation --output /tmp/peppermint.png
    .venv/bin/python scripts/preview_ui.py --scene approval --width 1000 --height 1000 \
        --output /tmp/peppermint-approval.png
    .venv/bin/python scripts/preview_ui.py --scene tasks --menu \
        --output /tmp/peppermint-menu.png

Scenes include tasks, basic and advanced diagnostics. A window appears briefly
on the current display, without taking keyboard focus, and closes after capture.
GTK draws the realized widgets to a Cairo surface, so other desktop windows do
not obscure the resulting screenshot.
For a headless display, prefix the command with ``xvfb-run -a`` if installed.
The production D-Bus adapter is replaced for the entire preview; no daemon,
conversation database, model, or computer tools are accessed. The screenshot
contains sample data, not a diagnostic result from the current computer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

# Allow invocation from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def scene_tasks(scene: str) -> list[dict]:
    """Small, deterministic examples of the production task payloads."""
    if scene == "empty":
        return []
    if scene in ("tasks", "basic", "advanced"):
        examples = [("working", 41, "Keep video playback smooth while local AI runs."),
                    ("approval", 40, "Find why my Steam game closes before the main menu."),
                    ("question", 39, "Review startup applications and explain what I can turn off."),
                    ("conversation", 38, "Compare local AI options for this computer.")]
        tasks = []
        for kind, task_id, idea in examples:
            task = scene_tasks(kind)[0]
            task.update(id=task_id, idea=idea, created_at="2026-09-08T18:55:00+00:00",
                        updated_at=f"2026-09-08T19:{task_id - 20:02d}:00+00:00")
            tasks.append(task)
        return tasks

    idea = "Keep video playback smooth while local AI runs."
    task = {
        "id": 1,
        "idea": idea,
        "status": "done",
        "messages": [{"role": "user", "content": idea}],
        "steps": [],
        "plan": [],
    }
    if scene == "conversation":
        task["messages"] += [
            {"role": "assistant", "content": "We can compare a smaller local model, fewer concurrent requests, and browser AI."},
            {"role": "user", "content": "Keep both video windows open. Start with the local model option."},
            {"role": "assistant", "content": "Next, check available memory and the installed models. Each check will ask for permission."},
        ]
    elif scene == "approval":
        task["status"] = "awaiting-confirmation"
        task["messages"] += [{
            "role": "assistant",
            "content": "First, check available memory. This reads current usage and does not change settings.",
        }]
        task["pending"] = {
            "id": 101,
            "description": "Run command: free -h\nPurpose: inspect available RAM before comparing options.",
        }
        task["plan"] = [
            {"description": "Check available memory", "status": "in_progress"},
            {"description": "Compare local and browser AI options", "status": "pending"},
        ]
    elif scene == "question":
        task["idea"] = "Help me clean up my computer."
        task["messages"] = [{"role": "user", "content": task["idea"]}]
        task["status"] = "awaiting-input"
        task["question"] = "What would you like to improve first?"
        task["steps"] = [{
            "tool": "ask_user",
            "status": "asked",
            "args": {
                "question": task["question"],
                "options": ["Review files taking up space", "Review startup applications"],
            },
        }]
    elif scene == "retest":
        task['idea'] = 'Game 480 closes before its main menu.'
        task['messages'] = [{'role': 'user', 'content': task['idea']}]
        task['status'] = 'awaiting-input'
        task['question'] = 'Retest: Launch game 480 and check whether its main menu stays open.'
        request = {
            'version': 1, 'request_id': 'a' * 32, 'verification_target_id': 'b' * 32,
            'verification_step': 2, 'target_description': 'Retest game 480 reaching its main menu',
            'symptom': 'Launch game 480 and check whether its main menu stays open.',
            'question': task['question'], 'outcome': None,
        }
        task['retest'] = request
        task['plan'] = [
            {'description': 'Inspect game 480', 'kind': 'inspection', 'status': 'done',
             'evidence_kind': 'inspection', 'evidence_tool': 'steam_game_diagnostics', 'evidence_step_id': 1},
            {'description': request['target_description'], 'kind': 'verification', 'status': 'pending',
             'verification_target_id': request['verification_target_id']},
        ]
        task['steps'] = [
            {'id': 1, 'tool': 'steam_game_diagnostics', 'status': 'ok', 'args': {'app_id': '480'},
             'output': 'Illustrative inspection; no computer diagnostic ran in this preview.'},
            {'id': 2, 'tool': 'request_retest', 'status': 'asked',
             'args': {'verification_step': 2, 'symptom': request['symptom']}, 'output': json.dumps(request)},
        ]
    elif scene == "working":
        task["status"] = "running"
        task["messages"] += [{
            "role": "assistant",
            "content": "The approved checks are complete. I’m comparing options against your goal of keeping both videos smooth.",
        }]
        task["plan"] = [
            {"description": "Inspect hardware and current resource usage", "status": "done",
             "kind": "inspection", "evidence_kind": "inspection",
             "evidence_tool": "performance_snapshot", "evidence_step_id": 1},
            {"description": "Compare options and explain tradeoffs", "status": "in_progress"},
            {"description": "Propose a playback test for your approval", "status": "pending"},
        ]
        task["steps"] = [{
            "id": 1, "tool": "performance_snapshot", "status": "ok", "args": {},
            "output": "Illustrative completed inspection; no diagnostic ran in this preview.",
        }]
    return [task]


def diagnostic_samples():
    """Illustrative measurements only; no sampler or system access."""
    from datetime import datetime, timedelta, timezone
    import math
    gib, mib = 1024 ** 3, 1024 ** 2
    for index in range(90):
        wave = math.sin(index / 7)
        cpu = 28 + 12 * wave + (12 if 38 <= index <= 50 else 0)
        yield {
            "schema_version": 1, "monotonic": 1000 + index * 2,
            "timestamp": (datetime(2026, 9, 8, 19, 20, tzinfo=timezone.utc) + timedelta(seconds=index * 2)).isoformat(),
            "elapsed_s": 2,
            "cpu": {"percent": cpu, "logical_count": 12, "load_avg": [2.4, 2.1, 1.8],
                    "cores": [{"id": c, "percent": max(0, cpu + 8 * math.sin(index / 4 + c))} for c in range(12)]},
            "memory": {"used_bytes": (8.6 + wave / 3) * gib, "total_bytes": 16 * gib,
                       "available_bytes": (7.4 - wave / 3) * gib,
                       "swap_used_bytes": 0.15 * gib, "swap_total_bytes": 2 * gib,
                       "swap_in_bytes_per_s": 0, "swap_out_bytes_per_s": 0},
            "gpu": {"status": "available", "devices": [{"id": "preview-gpu", "name": "Example GPU",
                    "utilization_pct": 38 + wave * 20, "memory_used_bytes": 4.2 * gib,
                    "memory_total_bytes": 8 * gib, "temperature_c": 54}]},
            "disk": {"read_bytes_per_s": (2 + wave) * mib, "write_bytes_per_s": (0.5 + wave / 3) * mib,
                     "scope": "Whole physical devices", "devices": [{"name": "nvme0n1", "busy_pct": 3.4,
                         "read_bytes_per_s": (2 + wave) * mib, "write_bytes_per_s": (0.5 + wave / 3) * mib}]},
            "network": {"rx_bytes_per_s": (5 + wave) * mib, "tx_bytes_per_s": (0.4 + wave / 4) * mib,
                        "scope": "Physical interfaces", "interfaces": [{"name": "enp4s0",
                         "rx_bytes_per_s": (5 + wave) * mib, "tx_bytes_per_s": (0.4 + wave / 4) * mib}]},
            "pressure": {key: {"some": {"avg10": value}, "full": {"avg10": 0}} for key, value in
                         (("cpu", 0.9), ("memory", 0.1), ("io", 0.2))},
            "temperatures": [{"label": "CPU package", "celsius": 48.5}], "errors": [],
            "processes": {"total_count": 4, "scanned": 4, "limited": False, "items": [
                {"pid": pid, "start_ticks": pid * 1000, "name": name, "state": "S", "cpu_pct": usage,
                 "rss_bytes": rss * mib, "read_bytes_per_s": 0, "write_bytes_per_s": 0}
                for pid, name, usage, rss in ((1428, "ollama", 146.2, 4200), (2093, "firefox", 37.4, 1580),
                                              (3210, "steam", 2.8, 290), (4082, "peppermint", 1.2, 110))]},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", choices=("empty", "conversation", "approval", "question", "retest", "working", "tasks", "basic", "advanced", "manual"), default="conversation")
    parser.add_argument("--width", type=int, default=1000, help="Requested window width (minimum 660)")
    parser.add_argument("--height", type=int, default=1000, help="Requested window height (minimum 560)")
    parser.add_argument("--output", required=True, type=Path, help="Destination PNG file")
    parser.add_argument("--menu", action="store_true", help="Show the sliding peppermint sidebar")
    args = parser.parse_args()
    if args.width < 660 or args.height < 560:
        parser.error("Peppermint's minimum window size is 660 × 560.")

    import gi
    import cairo

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    if not Gtk.init_check()[0]:
        parser.error("No GTK display is available. Run in a desktop session or use xvfb-run -a.")

    from peppermint.common import dbus_api
    from peppermint.ui.window import PeppermintWindow

    tasks = scene_tasks(args.scene)

    class PreviewBus:
        def signal_subscribe(self, *_args):
            return 0

    def fixture_call(method, params=None, *_args, **_kwargs):
        if method == "ListTasks":
            payload = tasks
        elif method == "TaskOverview":
            payload = {"tasks": tasks, "counts": {status: sum(t['status'] == status for t in tasks)
                       for status in {t['status'] for t in tasks}}, "total": len(tasks),
                       "matched": len(tasks), "offset": 0, "limit": 40, "has_more": False}
        elif method == "GetTask":
            task_id = params.unpack()[0]
            payload = next((task for task in tasks if task["id"] == task_id), {})
        else:
            raise RuntimeError(f"Preview cannot execute {method}; all actions are disabled.")
        return GLib.Variant("(s)", (json.dumps(payload),))

    class PreviewReader:
        def request(self, _key, method, params, callback):
            callback(json.loads(fixture_call(method, params).unpack()[0]), None)

        def close(self):
            pass

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    with patch.object(dbus_api, "session_bus", return_value=PreviewBus()), \
            patch.object(dbus_api, "call_daemon", side_effect=fixture_call):
        window = PeppermintWindow(None, reader=PreviewReader())
        window.set_default_size(args.width, args.height)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.set_focus_on_map(False)
        window.set_accept_focus(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.status_dot.set_text("● Preview · sample data")
        window.conversation_count.set_text(f"{len(tasks)} sample conversation{'s' if len(tasks) != 1 else ''}")
        for task in tasks:
            row = window.rows[task["id"]]
            row.revealer.set_transition_duration(0)
            row.expand()
        if args.scene in ('basic', 'advanced'):
            window.pages.set_visible_child_name('diagnostics')
            for sample in diagnostic_samples():
                window.diagnostics.ingest_sample(sample)
            window.diagnostics.set_task_context(tasks[0])
            window.diagnostics.advanced_button.set_active(args.scene == 'advanced')
        elif args.scene == 'manual':
            window.pages.set_visible_child_name('manual')
        elif args.scene != 'tasks':
            window.pages.set_visible_child_name('conversations')
        window.show_all()
        if args.menu:
            window.menu_button.set_active(True)

        def capture():
            try:
                width, height = window.get_allocated_width(), window.get_allocated_height()
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                context = cairo.Context(surface)
                window.draw(context)
                # TreeView headers have their own native GDK windows and GTK's
                # root-widget draw omits them. Render those actual buttons too.
                if args.scene == 'advanced':
                    tree = window.diagnostics.process_tree
                    position = tree.translate_coordinates(window, 0, 0)
                    if position:
                        context.save()
                        context.rectangle(position[0], position[1], tree.get_allocated_width(), 60)
                        context.clip()
                        for column in tree.get_columns():
                            button = column.get_button()
                            origin = button.translate_coordinates(window, 0, 0) if button else None
                            if button and origin and button.get_mapped():
                                context.save()
                                context.translate(*origin)
                                button.draw(context)
                                context.restore()
                        context.restore()
                pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)
                if pixbuf is None:
                    raise RuntimeError("GTK could not capture the preview window.")
                pixbuf.savev(str(output), "png", [], [])
                print(f"Saved {args.scene} preview: {output} ({pixbuf.get_width()} × {pixbuf.get_height()})")
            except Exception as exc:
                failures.append(exc)
            finally:
                window.destroy()
                Gtk.main_quit()
            return GLib.SOURCE_REMOVE

        # Allow GTK layout and state transitions to settle before widget rendering.
        GLib.timeout_add(250, lambda: (window.status_dot.set_text("● Preview · sample data"), GLib.SOURCE_REMOVE)[1])
        GLib.timeout_add(500, capture)
        Gtk.main()
    if failures:
        parser.exit(1, f"Preview failed: {failures[0]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
