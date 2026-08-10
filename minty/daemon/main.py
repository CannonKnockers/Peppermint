"""The Minty daemon.

The daemon owns the task queue, the agent loop, and the database. It runs a
GLib main loop for D-Bus and one worker thread for the model. The model is
slow, so it must never block the bus.
"""

from __future__ import annotations

import json
import logging
import queue
import signal
import sys
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from minty import config  # noqa: E402
from minty.common import dbus_api  # noqa: E402
from minty.common.models import Status  # noqa: E402
from minty.daemon import notifier, undo  # noqa: E402
from minty.daemon.agent import Agent  # noqa: E402
from minty.daemon.db import Database  # noqa: E402
from minty.daemon.llm import LLM, LLMError  # noqa: E402

log = logging.getLogger("minty.daemon")


def setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr),
                  logging.FileHandler(config.LOG_PATH)],
    )


class Job:
    """One unit of work for the worker thread."""

    def __init__(self, kind: str, task_id: int, payload=None):
        self.kind = kind          # run | confirm | answer | chat
        self.task_id = task_id
        self.payload = payload


class Daemon:
    def __init__(self):
        self.db = Database()
        self.llm = LLM()
        self.agent = Agent(self.db, self.llm, on_update=self._emit_update)
        self.jobs: queue.Queue[Job | None] = queue.Queue()
        self.loop = GLib.MainLoop()
        self.connection: Gio.DBusConnection | None = None
        self.registration_id = 0
        self.worker = threading.Thread(target=self._work, name="minty-agent", daemon=True)

    # --- start and stop ----------------------------------------------------

    def start(self) -> int:
        if not self.db.integrity_ok():
            log.error("The Minty database fails its integrity check. Minty stops "
                      "rather than work from damaged records.")
            return 1
        log.info("Database schema version %s.", self.db.current_version())

        recovered = self.db.recover()
        if recovered["resumed"]:
            log.info("Put %d unfinished tasks back in the queue: %s",
                     len(recovered["resumed"]), recovered["resumed"])
        for task_id in recovered["halted"]:
            task = self.db.get_task(task_id, with_steps=False)
            log.warning("Task %s stopped part way through a change. Minty did not "
                        "repeat it.", task_id)
            notifier.send(
                "Minty stopped part way through a change",
                f"{task.idea[:100]}\nCheck the result before you ask again.",
                urgent=True, action_label="Open",
            )

        try:
            models = self.llm.available_models()
            log.info("Ollama answers. Models: %s", ", ".join(models) or "none")
        except LLMError as exc:
            log.warning("%s", exc)

        Gio.bus_own_name(
            Gio.BusType.SESSION,
            dbus_api.DAEMON_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            lambda *_: log.info("Minty owns %s", dbus_api.DAEMON_NAME),
            self._on_name_lost,
        )

        self.worker.start()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)

        # Pick up tasks that were queued while the daemon was down.
        GLib.idle_add(self._drain_queued)
        log.info("Minty is ready.")
        self.loop.run()
        return 0

    def _on_signal(self) -> bool:
        log.info("Minty stops.")
        self.jobs.put(None)
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _on_name_lost(self, connection, name):
        if connection is None:
            log.error("Minty cannot reach the session bus.")
        else:
            log.error("Another Minty daemon already runs.")
        self.loop.quit()

    # --- D-Bus -------------------------------------------------------------

    def _on_bus_acquired(self, connection, name):
        self.connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(dbus_api.DAEMON_XML)
        self.registration_id = connection.register_object(
            dbus_api.DAEMON_PATH, node.interfaces[0], self._on_method, None, None
        )

    def _on_method(self, connection, sender, path, iface, method, params, invocation):
        try:
            result = self._dispatch(method, params.unpack())
        except Exception as exc:
            log.exception("The method %s failed", method)
            invocation.return_dbus_error("org.minty.Error", f"{type(exc).__name__}: {exc}")
            return
        invocation.return_value(result)

    def _dispatch(self, method: str, args: tuple):
        if method == "AddTask":
            task_id = self.db.add_task(args[0].strip())
            log.info("New task %s: %s", task_id, args[0][:80])
            self.jobs.put(Job("run", task_id))
            self._emit_update(task_id, Status.QUEUED)
            return GLib.Variant("(i)", (task_id,))

        if method == "ListTasks":
            limit = args[0] or 50
            tasks = [t.to_dict() for t in self.db.list_tasks(limit)]
            return GLib.Variant("(s)", (json.dumps(tasks),))

        if method == "GetTask":
            task = self.db.get_task(args[0])
            return GLib.Variant("(s)", (task.to_json() if task else "null",))

        if method == "Confirm":
            task_id, approved = int(args[0]), bool(args[1])
            self.jobs.put(Job("confirm", task_id, approved))
            return None

        if method == "Answer":
            self.jobs.put(Job("answer", int(args[0]), args[1]))
            return None

        if method == "Chat":
            self.jobs.put(Job("chat", int(args[0]), args[1]))
            return None

        if method == "Cancel":
            task_id = int(args[0])
            self.db.set_status(task_id, Status.CANCELLED)
            self._emit_update(task_id, Status.CANCELLED)
            return None

        if method == "Undo":
            return GLib.Variant("(s)", (json.dumps(self.db.last_undo(args[0] or 20)),))

        if method == "Revert":
            return GLib.Variant("(s)", (json.dumps(self._revert(int(args[0]), int(args[1]))),))

        if method == "Health":
            try:
                models = self.llm.available_models()
                ok = True
                detail = ", ".join(models)
            except LLMError as exc:
                ok, detail = False, str(exc)
            payload = {"ok": ok, "model": self.llm.model, "models": detail,
                       "queue": self.jobs.qsize(), "db": str(config.DB_PATH)}
            return GLib.Variant("(s)", (json.dumps(payload),))

        raise ValueError(f"There is no method named {method}.")

    def _revert(self, undo_id: int, task_id: int) -> list[dict]:
        """Put back one recorded change, or every change of one task.

        The newest change goes back first. A move made after another move must
        be undone before the earlier one, or the paths will not match.
        """
        if undo_id:
            records = [r for r in self.db.last_undo(200, include_done=True) if r["id"] == undo_id]
        elif task_id:
            records = self.db.last_undo(200, task_id=task_id)
        else:
            records = self.db.last_undo(1)

        outcome = []
        for record in records:
            if record.get("undone"):
                outcome.append({"id": record["id"], "ok": False,
                                "message": "This change was already put back."})
                continue
            # Claim the record first. A second caller then finds nothing to do.
            if not self.db.mark_undone(record["id"]):
                outcome.append({"id": record["id"], "ok": False,
                                "message": "Another window put this change back first."})
                continue

            result = undo.revert(record)
            if not result.ok:
                # It did not go back, so it is not undone.
                with self.db.connection() as conn:
                    conn.execute("UPDATE undo SET undone = 0 WHERE id = ?", (record["id"],))
            outcome.append({"id": record["id"], "ok": result.ok, "message": result.message,
                            "what": undo.describe(record)})
            log.info("Revert %s: %s", record["id"], result.message)
        return outcome

    def _emit_update(self, task_id: int, status) -> None:
        """Send TaskUpdated. The worker thread calls this, so hop to the main loop."""
        value = status.value if hasattr(status, "value") else str(status)

        def emit():
            if self.connection is not None:
                self.connection.emit_signal(
                    None, dbus_api.DAEMON_PATH, dbus_api.DAEMON_IFACE, "TaskUpdated",
                    GLib.Variant("(is)", (task_id, value)),
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(emit)

    # --- the worker --------------------------------------------------------

    def _drain_queued(self) -> bool:
        while True:
            task = self.db.next_queued()
            if task is None:
                break
            self.jobs.put(Job("run", task.id))
            # Mark it so the next call does not queue the same task twice.
            self.db.set_status(task.id, Status.PLANNING)
        return GLib.SOURCE_REMOVE

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception:
                log.exception("The job for task %s failed", job.task_id)
                self.db.set_status(job.task_id, Status.FAILED,
                                   error="Minty hit an internal fault. See the log.")
                self._emit_update(job.task_id, Status.FAILED)
            finally:
                self.jobs.task_done()

    def _run_job(self, job: Job) -> None:
        if job.kind == "run":
            result = self.agent.run(job.task_id)
        elif job.kind == "confirm":
            result = self.agent.resume_after_confirm(job.task_id, bool(job.payload))
        elif job.kind == "answer":
            result = self.agent.resume_after_answer(job.task_id, str(job.payload))
        elif job.kind == "chat":
            result = self.agent.follow_up(job.task_id, str(job.payload))
        else:
            return

        self._announce(job.task_id, result)

    def _announce(self, task_id: int, result) -> None:
        task = self.db.get_task(task_id, with_steps=False)
        idea = (task.idea[:60] + "...") if task and len(task.idea) > 60 else (task.idea if task else "")

        if result.status is Status.DONE:
            notifier.send("Minty finished", f"{idea}\n{result.text[:180]}", action_label="Open")
        elif result.status is Status.AWAITING_CONFIRMATION:
            notifier.send("Minty needs your approval", result.text[:180], urgent=True, action_label="Open")
        elif result.status is Status.AWAITING_INPUT:
            notifier.send("Minty has a question", result.text[:180], urgent=True, action_label="Open")
        elif result.status is Status.FAILED:
            notifier.send("Minty could not finish", f"{idea}\n{result.text[:180]}", action_label="Open")


def main() -> int:
    setup_logging()
    return Daemon().start()


if __name__ == "__main__":
    raise SystemExit(main())
