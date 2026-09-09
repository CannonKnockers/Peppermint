"""SQLite storage for tasks, steps, messages, confirmations, and undo data.

The daemon runs a GLib main loop and one worker thread. Both touch the
database, so every connection uses WAL mode and a busy timeout.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

from peppermint import config
from peppermint.common.models import Confirmation, Status, Step, Task
from peppermint.common.retest import pending_retest

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    idea        TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    parent_task_id INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    result      TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT '',
    question    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS steps (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tool     TEXT    NOT NULL,
    args     TEXT    NOT NULL DEFAULT '{}',
    risk     TEXT    NOT NULL DEFAULT 'safe',
    output   TEXT    NOT NULL DEFAULT '',
    status   TEXT    NOT NULL DEFAULT 'ok',
    ts       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role     TEXT    NOT NULL,
    content  TEXT    NOT NULL,
    ts       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id      INTEGER NOT NULL DEFAULT 0,
    description  TEXT    NOT NULL,
    reason       TEXT    NOT NULL DEFAULT '',
    resolved     INTEGER NOT NULL DEFAULT 0,
    approved     INTEGER NOT NULL DEFAULT 0,
    ts           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS undo (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id   INTEGER NOT NULL,
    kind      TEXT    NOT NULL,
    target    TEXT    NOT NULL,
    old_value TEXT    NOT NULL DEFAULT '',
    ts        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    calls_used INTEGER NOT NULL DEFAULT 0,
    step_start INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_plans (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    steps TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_schedules (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    schedule_text TEXT NOT NULL,
    backend TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    on_calendar TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    last_run_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_confirm_task ON confirmations(task_id, resolved);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_task_schedules_enabled ON task_schedules(enabled);
"""


SCHEMA_VERSION = 5

# Each migration takes a connection and moves the database up by one version.
# A migration must be safe to run on a database that real work already used.
MIGRATIONS: dict[int, list[str]] = {
    2: [
        # An approval now names the exact action it authorises, and expires.
        "ALTER TABLE confirmations ADD COLUMN token TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE confirmations ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE confirmations ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''",
        # A step records what Peppermint was doing when the power went out.
        "ALTER TABLE steps ADD COLUMN started_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE steps ADD COLUMN mutating INTEGER NOT NULL DEFAULT 0",
        # Every task records the rules it ran under.
        "ALTER TABLE tasks ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0",
    ],
    3: [
        # A change can be put back, and only once.
        "ALTER TABLE undo ADD COLUMN undone INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE undo ADD COLUMN step_id INTEGER NOT NULL DEFAULT 0",
    ],
    4: [
        "ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER NOT NULL DEFAULT 0",
    ],
    5: [
        "CREATE TABLE IF NOT EXISTS task_schedules ("
        "    task_id INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,"
        "    schedule_text TEXT NOT NULL,"
        "    backend TEXT NOT NULL,"
        "    cron_expr TEXT NOT NULL,"
        "    on_calendar TEXT NOT NULL,"
        "    enabled INTEGER NOT NULL DEFAULT 0,"
        "    last_run_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,"
        "    created_at TEXT NOT NULL,"
        "    updated_at TEXT NOT NULL"
        ")",
        "CREATE INDEX IF NOT EXISTS idx_task_schedules_enabled ON task_schedules(enabled)",
    ],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _archive_transaction(conn):
    """Give an archive operation its own rollback boundary, including in a caller transaction."""
    conn.execute("SAVEPOINT peppermint_archive")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO peppermint_archive")
        conn.execute("RELEASE peppermint_archive")
        raise
    else:
        conn.execute("RELEASE peppermint_archive")


_ARCHIVE_FIELDS = {
    "tasks": {
        "idea": str, "status": str, "created_at": str, "updated_at": str,
        "result": (str, ""), "error": (str, ""), "question": (str, ""),
        "parent_task_id": (int, 0),
        "policy_version": (int, 0),
    },
    "steps": {
        "tool": str, "args": dict, "ts": str, "risk": (str, "safe"),
        "output": (str, ""), "status": (str, "ok"), "started_at": (str, ""),
        "mutating": (int, 0),
    },
    "messages": {"role": str, "content": str, "ts": str},
    "confirmations": {
        "description": str, "ts": str, "step_id": (int, 0), "reason": (str, ""),
        "resolved": (int, 0), "approved": (int, 0), "token": (str, ""),
        "expires_at": (str, ""), "fingerprint": (str, ""),
    },
    "undo": {
        "kind": str, "target": str, "ts": str, "old_value": (str, ""),
        "undone": (int, 0), "step_id": (int, 0),
    },
    "task_runs": {"calls_used": int, "step_start": int},
}
_ARCHIVE_CHILDREN = {"steps": "steps", "conversations": "messages",
                     "approvals": "confirmations", "undo": "undo"}
_SQLITE_MAX_ID = 2**63 - 1


def _archive_integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= _SQLITE_MAX_ID:
        raise ValueError(f"{label} must be an integer from {minimum} to {_SQLITE_MAX_ID}.")
    return value


def _archive_row(row, table, task_id=None):
    if not isinstance(row, dict):
        raise ValueError(f"Archived {table} row must be an object.")
    result = {}
    if table != "task_runs":
        result["id"] = _archive_integer(row.get("id"), f"{table}.id", 1)
    if task_id is not None:
        result["task_id"] = _archive_integer(row.get("task_id"), f"{table}.task_id", 1)
        if result["task_id"] != task_id:
            raise ValueError(f"Archived {table} row belongs to another task.")
    for key, spec in _ARCHIVE_FIELDS[table].items():
        kind, value = (spec[0], row.get(key, spec[1])) if isinstance(spec, tuple) else (spec, row.get(key))
        if kind is int:
            value = _archive_integer(value, f"{table}.{key}")
        elif not isinstance(value, kind):
            raise ValueError(f"Archived {table}.{key} must be {kind.__name__}.")
        if key in ("mutating", "resolved", "approved", "undone") and value not in (0, 1):
            raise ValueError(f"Archived {table}.{key} must be 0 or 1.")
        result[key] = deepcopy(value)
    return result


def _archive_reference(value, step_ids, label):
    value = _archive_integer(value, label)
    if value and value not in step_ids:
        raise ValueError(f"Archived {label} refers to a missing step in its task.")
    return value


def _archive_tasks(tasks):
    """Validate relational data before it can be inserted into the live database."""
    if not isinstance(tasks, list):
        raise ValueError("Archived tasks must be an array.")
    seen = {table: set() for table in ("tasks", *_ARCHIVE_CHILDREN.values())}
    normalized = []
    for source in tasks:
        task = _archive_row(source, "tasks")
        task_id = task["id"]
        if task_id in seen["tasks"]:
            raise ValueError("Archived task IDs must be unique.")
        seen["tasks"].add(task_id)
        try:
            Status(task["status"])
        except ValueError as exc:
            raise ValueError("Archived task has an unknown status.") from exc
        for name, table in _ARCHIVE_CHILDREN.items():
            rows = source.get(name, [] if name == "undo" else None)
            if not isinstance(rows, list):
                raise ValueError(f"Archived {name} must be an array.")
            task[name] = []
            for row in rows:
                row = _archive_row(row, table, task_id)
                if row["id"] in seen[table]:
                    raise ValueError(f"Archived {table} IDs must be unique.")
                seen[table].add(row["id"])
                task[name].append(row)
            task[name].sort(key=lambda row: row["id"])
        step_ids = {row["id"] for row in task["steps"]}
        for name in ("approvals", "undo"):
            for row in task[name]:
                _archive_reference(row["step_id"], step_ids, name + ".step_id")
        task["plan"] = deepcopy(source.get("plan", []))
        if not isinstance(task["plan"], list) or any(not isinstance(row, dict) for row in task["plan"]):
            raise ValueError("Archived plan must be an array of objects.")
        for row in task["plan"]:
            if "evidence_step_id" in row:
                _archive_reference(row["evidence_step_id"], step_ids, "plan.evidence_step_id")
        task["run"] = None
        if source.get("run") is not None:
            task["run"] = _archive_row(source["run"], "task_runs", task_id)
            _archive_reference(task["run"]["step_start"], step_ids, "run.step_start")
        # Reject non-JSON arguments, plans, and non-finite numbers before any writes.
        try:
            json.dumps(task, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Archived task history must contain valid JSON values.") from exc
        normalized.append(task)
    return normalized, seen


def _remap_history_ids(value, step_ids):
    """Update known structured evidence fields, never arbitrary prose or numeric tool arguments."""
    if isinstance(value, list):
        return [_remap_history_ids(item, step_ids) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: step_ids.get(item, item) if key == "evidence_step_id" and type(item) is int
            else _remap_history_ids(item, step_ids) for key, item in value.items()}


def _remap_retest_message(content, step_ids):
    try:
        message = json.loads(content)
        if not isinstance(message, dict) or message.get("name") != "request_retest":
            return content
        receipt = json.loads(message["content"])
        remapped = _remap_history_ids(receipt, step_ids)
        if remapped == receipt:
            return content
        message["content"] = json.dumps(remapped, ensure_ascii=False)
        return json.dumps(message, ensure_ascii=False)
    except (ValueError, TypeError, KeyError):
        return content


class Database:
    def __init__(self, path=None):
        self.path = str(path or config.DB_PATH)
        if self.path != ":memory:":
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._shared = None
        if self.path == ":memory:":
            # An in-memory database dies with its connection, so tests share one.
            self._shared = self._make_connection()
        with self.connection() as conn:
            conn.executescript(SCHEMA)
        self.migrate()

    # --- migrations --------------------------------------------------------

    def current_version(self) -> int:
        row = self.connection().execute("SELECT version FROM schema_version").fetchone()
        return int(row["version"]) if row else 0

    def migrate(self) -> int:
        """Bring an old database up to the current schema. Safe to run twice."""
        conn = self.connection()
        version = self.current_version()

        if version == 0:
            # A database made before this table existed may already hold newer
            # columns, because the CREATE statements above are current.
            # Replay idempotent migrations: one newer column does not prove
            # that all earlier migrations were applied.
            version = 1
            with conn:
                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

        while version < SCHEMA_VERSION:
            step = version + 1
            for statement in MIGRATIONS.get(step, []):
                try:
                    with conn:
                        conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    # A column that is already there is not a failure.
                    if "duplicate column" not in str(exc).lower():
                        raise
            version = step
            with conn:
                conn.execute("UPDATE schema_version SET version = ?", (version,))
        return version

    def integrity_ok(self) -> bool:
        row = self.connection().execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"

    def _make_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def connection(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._make_connection()
            self._local.conn = conn
        return conn

    # --- tasks -------------------------------------------------------------

    def add_task(self, idea: str) -> int:
        ts = now()
        with self.connection() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (idea, status, created_at, updated_at) VALUES (?,?,?,?)",
                (idea, Status.QUEUED.value, ts, ts),
            )
            return int(cur.lastrowid)

    # --- recurring schedules -----------------------------------------------

    def get_schedule(self, task_id: int) -> dict | None:
        row = self.connection().execute(
            "SELECT * FROM task_schedules WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_schedules(self) -> list[dict]:
        return [dict(row) for row in self.connection().execute(
            "SELECT * FROM task_schedules ORDER BY task_id")]

    def add_schedule(self, task_id, text, backend, cron_expr, on_calendar):
        ts = now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO task_schedules "
                "(task_id, schedule_text, backend, cron_expr, on_calendar, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)", (task_id, text, backend, cron_expr, on_calendar, ts, ts))

    def set_schedule_enabled(self, task_id, enabled):
        with self.connection() as conn:
            conn.execute("UPDATE task_schedules SET enabled = ?, updated_at = ? WHERE task_id = ?",
                         (int(enabled), now(), task_id))

    def remove_schedule(self, task_id):
        with self.connection() as conn:
            conn.execute("DELETE FROM task_schedules WHERE task_id = ?", (task_id,))

    def create_scheduled_run(self, task_id: int) -> int:
        """Atomically claim an occurrence. Fresh history means fresh approvals.

        A paused/removed schedule or an unfinished template/previous run is a
        no-op. Keep the template and all previous run history unchanged.
        """
        conn = self.connection()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT s.*, t.idea, t.status FROM task_schedules s "
                "JOIN tasks t ON t.id = s.task_id WHERE s.task_id = ?", (task_id,)).fetchone()
            if row is None or not row['enabled'] or not Status(row['status']).is_final:
                return 0
            previous = conn.execute("SELECT status FROM tasks WHERE id = ?",
                                    (row['last_run_task_id'],)).fetchone()
            if previous and not Status(previous['status']).is_final:
                return 0
            ts = now()
            cur = conn.execute(
                "INSERT INTO tasks (idea, status, created_at, updated_at) VALUES (?,?,?,?)",
                (row['idea'], Status.QUEUED.value, ts, ts))
            new_id = int(cur.lastrowid)
            conn.execute("UPDATE task_schedules SET last_run_task_id = ?, updated_at = ? WHERE task_id = ?",
                         (new_id, ts, task_id))
            return new_id

    def set_status(self, task_id: int, status: Status, *, allow_cancelled: bool = False, **fields) -> None:
        cols = ["status = ?", "updated_at = ?"]
        vals: list = [status.value, now()]
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            vals.append(value)
        vals.append(task_id)
        with self.connection() as conn:
            condition = "" if allow_cancelled or status is Status.CANCELLED else " AND status != 'cancelled'"
            conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE id = ?{condition}", vals)
            if status.is_final:
                conn.execute("UPDATE steps SET status = 'cancelled', output = 'This approval is no longer active.' "
                             "WHERE task_id = ? AND status = 'pending'", (task_id,))
                conn.execute("UPDATE confirmations SET resolved = 1, approved = 0 "
                             "WHERE task_id = ? AND resolved = 0", (task_id,))

    def get_task(self, task_id: int, with_steps: bool = True) -> Task | None:
        conn = self.connection()
        row = conn.execute("SELECT t.*, s.schedule_text, s.enabled AS schedule_enabled FROM tasks t "
                           "LEFT JOIN task_schedules s ON s.task_id = t.id WHERE t.id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = self._row_to_task(row)
        if with_steps:
            task.messages = [m for m in self.get_messages(task_id)
                             if m.get("role") in ("user", "assistant")
                             and m.get("content") and not m.get("internal")]
            task.plan = self.get_plan(task_id)
            task.steps = self.get_steps(task_id)
            task.pending = self.pending_confirmation(task_id)
            if task.status == Status.AWAITING_INPUT.value:
                task.retest = pending_retest(task.steps, task.plan)
        return task

    def export_tasks_with_history(self, task_ids: list[int] | None = None) -> list[dict]:
        """Export one or more tasks with all history needed to restore them."""
        if task_ids is not None:
            if not isinstance(task_ids, list):
                raise ValueError("task_ids must be a list of integers.")
            if not all(isinstance(task_id, int) and task_id > 0 for task_id in task_ids):
                raise ValueError("task IDs must be positive integers.")
        conn = self.connection()
        if task_ids is None:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
        else:
            if not task_ids:
                return []
            placeholders = ",".join("?" for _ in task_ids)
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY id ASC",
                tuple(task_ids),
            ).fetchall()

        tasks = []
        for row in rows:
            task_id = int(row["id"])
            tasks.append(self._task_with_history(conn, task_id, row))
        return tasks

    @staticmethod
    def _task_with_history(conn, task_id: int, row: sqlite3.Row) -> dict:
        # Keep a row-oriented snapshot and include every history table that should
        # move with the task.
        conversations = [
            dict(message) for message in conn.execute(
                "SELECT * FROM messages WHERE task_id = ? ORDER BY id ASC", (task_id,)
            ).fetchall()
        ]
        steps = [
            dict(step) for step in conn.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY id ASC", (task_id,)
            ).fetchall()
        ]
        approvals = [
            dict(approval) for approval in conn.execute(
                "SELECT * FROM confirmations WHERE task_id = ? ORDER BY id ASC", (task_id,)
            ).fetchall()
        ]
        undo = [
            dict(entry) for entry in conn.execute(
                "SELECT * FROM undo WHERE task_id = ? ORDER BY id ASC", (task_id,)
            ).fetchall()
        ]
        run_row = conn.execute(
            "SELECT calls_used, step_start FROM task_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        plan_row = conn.execute(
            "SELECT steps FROM task_plans WHERE task_id = ?",
            (task_id,),
        ).fetchone()

        try:
            plan = json.loads(plan_row["steps"]) if plan_row else []
        except (TypeError, ValueError):
            plan = []

        task = {
            "id": int(row["id"]),
            "idea": row["idea"],
            "status": row["status"],
            "parent_task_id": int(row["parent_task_id"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "result": row["result"],
            "error": row["error"],
            "question": row["question"],
            "policy_version": int(row["policy_version"]),
            "steps": steps,
            "conversations": conversations,
            "approvals": approvals,
            "undo": undo,
            "run": None if run_row is None else {
                "task_id": task_id,
                "calls_used": int(run_row["calls_used"]),
                "step_start": int(run_row["step_start"]),
            },
            "plan": plan,
        }
        return task

    def import_task_from_portable(self, source: dict) -> int:
        """Insert one exported task and return the new task id."""
        validated, _ = _archive_tasks([source])
        task = validated[0]
        conn = self.connection()
        with conn:
            with _archive_transaction(conn):
                return self._insert_portable_task(conn, task)

    @staticmethod
    def _insert_portable_task(conn: sqlite3.Connection, task: dict) -> int:
        cur = conn.execute(
            "INSERT INTO tasks (idea, status, parent_task_id, created_at, updated_at, result, error, question, policy_version) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                task["idea"],
                task["status"],
                int(task["parent_task_id"]),
                task["created_at"],
                task["updated_at"],
                task["result"],
                task["error"],
                task["question"],
                int(task["policy_version"]),
            ),
        )
        new_task_id = int(cur.lastrowid)

        step_id_map: dict[int, int] = {}
        for step in task["steps"]:
            raw = json.dumps(step["args"], allow_nan=False)
            cur = conn.execute(
                "INSERT INTO steps (task_id, tool, args, risk, output, status, ts, started_at, mutating)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new_task_id,
                    step["tool"],
                    raw,
                    step["risk"],
                    step["output"],
                    step["status"],
                    step["ts"],
                    step["started_at"],
                    int(step["mutating"]),
                ),
            )
            step_id_map[int(step["id"])] = int(cur.lastrowid)

        for message in task["conversations"]:
            content = _remap_retest_message(message["content"], step_id_map)
            conn.execute(
                "INSERT INTO messages (task_id, role, content, ts) VALUES (?,?,?,?)",
                (new_task_id, message["role"], content, message["ts"]),
            )

        for approval in task["approvals"]:
            conn.execute(
                "INSERT INTO confirmations (task_id, step_id, description, reason, resolved, approved,"
                " ts, token, expires_at, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    new_task_id,
                    step_id_map.get(int(approval["step_id"]), int(approval["step_id"])),
                    approval["description"],
                    approval["reason"],
                    int(approval["resolved"]),
                    int(approval["approved"]),
                    approval["ts"],
                    approval["token"],
                    approval["expires_at"],
                    approval["fingerprint"],
                ),
            )

        for record in task["undo"]:
            conn.execute(
                "INSERT INTO undo (task_id, kind, target, old_value, ts, undone, step_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    new_task_id,
                    record["kind"],
                    record["target"],
                    record["old_value"],
                    record["ts"],
                    int(record["undone"]),
                    step_id_map.get(int(record["step_id"]), int(record["step_id"])),
                ),
            )

        if task["run"] is not None:
            conn.execute(
                "INSERT INTO task_runs (task_id, calls_used, step_start) "
                "VALUES (?,?,?)",
                (
                    new_task_id,
                    int(task["run"]["calls_used"]),
                    step_id_map.get(int(task["run"]["step_start"]), int(task["run"]["step_start"])),
                ),
            )

        remapped_plan = [_remap_history_ids(step, step_id_map) for step in task["plan"]]
        conn.execute(
            "INSERT INTO task_plans (task_id, steps) VALUES (?, ?)",
            (new_task_id, json.dumps(remapped_plan)),
        )
        return new_task_id

    def list_tasks(self, limit: int = 50) -> list[Task]:
        conn = self.connection()
        rows = conn.execute(
            "SELECT t.*, s.schedule_text, s.enabled AS schedule_enabled FROM tasks t "
            "LEFT JOIN task_schedules s ON s.task_id = t.id ORDER BY t.id DESC LIMIT ?", (limit,)
        ).fetchall()
        tasks = [self._row_to_task(r) for r in rows]
        for task in tasks:
            task.plan = self.get_plan(task.id)
            if Status(task.status).needs_user:
                task.pending = self.pending_confirmation(task.id)
            if task.status == Status.AWAITING_INPUT.value:
                task.retest = pending_retest(self.get_steps(task.id), task.plan)
        return tasks

    def task_overview(self, status_filter: str = "all", query: str = "",
                      offset: int = 0, limit: int = 40) -> dict:
        """Page task summaries and whole-database counts in one read snapshot.

        The overview carries saved plans but never loads conversation history,
        step logs, or approval controls. Open a task for its current details.
        """
        filters = {
            "all": (),
            "active": (Status.QUEUED.value, Status.PLANNING.value, Status.RUNNING.value),
            "waiting": (Status.AWAITING_CONFIRMATION.value, Status.AWAITING_INPUT.value),
            "finished": (Status.DONE.value, Status.FAILED.value, Status.CANCELLED.value),
            "failed": (Status.FAILED.value,),
        }
        if not isinstance(status_filter, str) or status_filter not in filters:
            raise ValueError("Task filter must be all, active, waiting, finished, or failed.")
        if not isinstance(query, str):
            raise ValueError("Task search must be text.")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (offset, limit)):
            raise ValueError("Task offset and limit must be integers.")
        query = query[:240]
        offset = min(max(0, offset), 2**63 - 1)
        limit = min(100, max(1, limit))
        clauses, params = [], []
        if filters[status_filter]:
            clauses.append("t.status IN (" + ",".join("?" for _ in filters[status_filter]) + ")")
            params.extend(filters[status_filter])
        conn = self.connection()
        if query:
            # SQLite's built-in LOWER/NOCASE only fold ASCII. Keep application
            # names searchable across Unicode while treating %, _ and \\ literally.
            conn.create_function("peppermint_casefold", 1, str.casefold, deterministic=True)
            escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("peppermint_casefold(t.idea) LIKE ? ESCAPE '\\'")
            params.append("%" + escaped + "%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        owns_snapshot = not conn.in_transaction
        if owns_snapshot:
            conn.execute("BEGIN")
        try:
            counts = {status.value: 0 for status in Status}
            for row in conn.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"):
                counts[row["status"]] = row["count"]
            matched = conn.execute("SELECT COUNT(*) FROM tasks t" + where, params).fetchone()[0]
            rows = conn.execute(
                "SELECT t.*, p.steps AS overview_plan, s.schedule_text, s.enabled AS schedule_enabled FROM tasks t "
                "LEFT JOIN task_schedules s ON s.task_id = t.id "
                "LEFT JOIN task_plans p ON p.task_id = t.id" + where +
                " ORDER BY t.updated_at DESC, t.id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            tasks = []
            for row in rows:
                task = self._row_to_task(row)
                task.plan = json.loads(row["overview_plan"]) if row["overview_plan"] else []
                tasks.append(task.to_dict())
            return {"tasks": tasks, "counts": counts, "total": sum(counts.values()),
                    "matched": matched, "offset": offset, "limit": limit,
                    "has_more": offset + len(tasks) < matched}
        finally:
            if owns_snapshot:
                conn.rollback()  # End this read-only snapshot without committing caller writes.

    def next_queued(self) -> Task | None:
        conn = self.connection()
        row = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY id ASC LIMIT 1",
            (Status.QUEUED.value,),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def recover(self) -> dict[str, list[int]]:
        """Decide what to do with tasks that were running when Peppermint stopped.

        A task whose last step only read something can start again safely.

        A task that was part way through a change cannot. Peppermint does not know
        whether the change happened, and repeating it could do it twice. Such
        a task stops and says so, so the user can look before deciding.

        Returns the two lists, so the caller can log and report them.
        """
        conn = self.connection()
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status IN (?,?)",
            (Status.RUNNING.value, Status.PLANNING.value),
        ).fetchall()

        resumed: list[int] = []
        halted: list[int] = []

        for row in rows:
            task_id = int(row["id"])
            unfinished = self.unfinished_mutations(task_id)
            if unfinished:
                step = unfinished[0]
                detail = ", ".join(f"{k}={v}" for k, v in list(step.args.items())[:3])
                message = (
                    f"Peppermint stopped while it was running `{step.tool}` ({detail}). "
                    "It does not know whether that change finished, so it did not "
                    "try again. Check the result before you ask for it once more."
                )
                self.update_step(step.id, message, "interrupted")
                self.set_status(task_id, Status.FAILED, error=message)
                halted.append(task_id)
            else:
                self.set_status(task_id, Status.QUEUED)
                resumed.append(task_id)

        return {"resumed": resumed, "halted": halted}

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        task = Task(
            id=int(row["id"]),
            idea=row["idea"],
            status=row["status"],
            parent_task_id=int(row["parent_task_id"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=row["result"],
            error=row["error"],
            question=row["question"],
        )
        if 'schedule_text' in row.keys() and row['schedule_text']:
            task.schedule = {'schedule_text': row['schedule_text'], 'enabled': bool(row['schedule_enabled'])}
        from peppermint.common.secrets import history_secrets
        task._display_secrets = history_secrets(self.get_messages(task.id))
        return task

    def _ensure_task_graph_is_acyclic(self) -> None:
        conn = self.connection()
        rows = conn.execute("SELECT id, parent_task_id FROM tasks").fetchall()
        parent = {int(row["id"]): int(row["parent_task_id"]) for row in rows}
        for start_id in parent:
            seen = set()
            current = start_id
            while current:
                if current in seen:
                    raise ValueError("Task ancestry must be a directed acyclic graph.")
                seen.add(current)
                current = parent.get(current, 0)

    def fork_task(self, from_task_id: int, from_step_index: int, new_idea: str) -> int:
        if type(from_task_id) is not int or not 1 <= from_task_id <= 2_147_483_647:
            raise ValueError("Task ID must be between 1 and 2147483647.")
        if type(from_step_index) is not int or from_step_index < 0:
            raise ValueError("from_step_index must be an integer >= 0.")

        new_idea = (new_idea or "").strip()
        if not new_idea:
            raise ValueError("The fork idea must be a non-empty string.")

        conn = self.connection()
        source = conn.execute("SELECT * FROM tasks WHERE id = ?", (from_task_id,)).fetchone()
        if source is None:
            raise ValueError(f"Cannot fork unknown task {from_task_id}.")

        source_steps = conn.execute(
            "SELECT * FROM steps WHERE task_id = ? ORDER BY id ASC",
            (from_task_id,),
        ).fetchall()
        self._ensure_task_graph_is_acyclic()
        if not source_steps:
            raise ValueError("Source task has no steps to fork.")
        if from_step_index >= len(source_steps):
            raise ValueError("Fork index must be within the source task's step range.")

        with conn:
            new_task = conn.execute(
                "INSERT INTO tasks (idea, status, parent_task_id, created_at, updated_at, result, error, question, policy_version)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new_idea,
                    Status.QUEUED.value,
                    from_task_id,
                    now(),
                    now(),
                    "",
                    "",
                    "",
                    int(source["policy_version"]),
                ),
            )
            new_task_id = int(new_task.lastrowid)

            step_id_map: dict[int, int] = {}
            for step in source_steps[:from_step_index + 1]:
                try:
                    args = json.loads(step["args"])
                except (TypeError, ValueError):
                    args = {}
                cur = conn.execute(
                    "INSERT INTO steps (task_id, tool, args, risk, output, status, ts, started_at, mutating)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        new_task_id,
                        step["tool"],
                        json.dumps(args, allow_nan=False),
                        step["risk"],
                        step["output"],
                        step["status"],
                        step["ts"],
                        step["started_at"],
                        int(step["mutating"]),
                    ),
                )
                step_id_map[int(step["id"])] = int(cur.lastrowid)

            for message in conn.execute(
                "SELECT role, content, ts FROM messages WHERE task_id = ? ORDER BY id ASC",
                (from_task_id,),
            ).fetchall():
                content = _remap_retest_message(message["content"], step_id_map)
                conn.execute(
                    "INSERT INTO messages (task_id, role, content, ts) VALUES (?,?,?,?)",
                    (new_task_id, message["role"], content, message["ts"]),
                )

            copied_steps = set(step_id_map)
            for confirmation in conn.execute(
                "SELECT * FROM confirmations WHERE task_id = ? ORDER BY id ASC",
                (from_task_id,),
            ).fetchall():
                confirmation_step = int(confirmation["step_id"])
                if confirmation_step and confirmation_step not in copied_steps:
                    continue
                conn.execute(
                    "INSERT INTO confirmations (task_id, step_id, description, reason, resolved, approved, "
                    "ts, token, expires_at, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_task_id,
                        step_id_map.get(confirmation_step, confirmation_step),
                        confirmation["description"],
                        confirmation["reason"],
                        int(confirmation["resolved"]),
                        int(confirmation["approved"]),
                        confirmation["ts"],
                        confirmation["token"],
                        confirmation["expires_at"],
                        confirmation["fingerprint"],
                    ),
                )

            for record in conn.execute(
                "SELECT * FROM undo WHERE task_id = ? ORDER BY id ASC",
                (from_task_id,),
            ).fetchall():
                undo_step = int(record["step_id"])
                if undo_step and undo_step not in copied_steps:
                    continue
                conn.execute(
                    "INSERT INTO undo (task_id, kind, target, old_value, ts, undone, step_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        new_task_id,
                        record["kind"],
                        record["target"],
                        record["old_value"],
                        record["ts"],
                        int(record["undone"]),
                        step_id_map.get(undo_step, undo_step),
                    ),
                )

            source_run = conn.execute("SELECT calls_used, step_start FROM task_runs WHERE task_id = ?",
                                      (from_task_id,)).fetchone()
            if source_run is not None:
                last_step = max(step_id_map.values()) if step_id_map else 0
                mapped_start = step_id_map.get(int(source_run["step_start"]), 0)
                mapped_start = mapped_start or last_step
                conn.execute(
                    "INSERT INTO task_runs (task_id, calls_used, step_start) VALUES (?, ?, ?)",
                    (new_task_id, 0, mapped_start),
                )

            source_plan = conn.execute("SELECT steps FROM task_plans WHERE task_id = ?", (from_task_id,)).fetchone()
            if source_plan is not None:
                try:
                    plan = json.loads(source_plan["steps"])
                except (TypeError, ValueError):
                    plan = []
                if not isinstance(plan, list):
                    raise ValueError("Stored plan data must be an array.")
                conn.execute(
                    "INSERT INTO task_plans (task_id, steps) VALUES (?, ?)",
                    (new_task_id, json.dumps([_remap_history_ids(row, step_id_map) for row in plan])),
                )

            return new_task_id

    def ensure_run(self, task_id: int, reset: bool = False) -> None:
        with self.connection() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM steps WHERE task_id = ?", (task_id,)).fetchone()
            if reset:
                conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
            conn.execute("INSERT OR IGNORE INTO task_runs (task_id, step_start) VALUES (?, ?)",
                         (task_id, int(row[0])))

    def consume_model_call(self, task_id: int, limit: int) -> bool:
        with self.connection() as conn:
            result = conn.execute("UPDATE task_runs SET calls_used = calls_used + 1 "
                                  "WHERE task_id = ? AND calls_used < ?", (task_id, limit))
            return result.rowcount == 1

    def run_steps(self, task_id: int) -> list[Step]:
        row = self.connection().execute("SELECT step_start FROM task_runs WHERE task_id = ?", (task_id,)).fetchone()
        start = int(row[0]) if row else 0
        return [step for step in self.get_steps(task_id) if step.id > start]

    def get_plan(self, task_id: int) -> list[dict]:
        row = self.connection().execute("SELECT steps FROM task_plans WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row['steps']) if row else []

    def set_plan(self, task_id: int, steps: list[dict]) -> None:
        with self.connection() as conn:
            conn.execute("INSERT INTO task_plans (task_id, steps) VALUES (?, ?) "
                         "ON CONFLICT(task_id) DO UPDATE SET steps = excluded.steps",
                         (task_id, json.dumps(steps)))

    # --- steps -------------------------------------------------------------

    def add_step(self, task_id: int, tool: str, args: dict, risk: str,
                 output: str = "", status: str = "ok", mutating: bool = False) -> int:
        with self.connection() as conn:
            cur = conn.execute(
                "INSERT INTO steps"
                " (task_id, tool, args, risk, output, status, ts, started_at, mutating)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (task_id, tool, json.dumps(args), risk, output, status, now(),
                 now() if status == "executing" else "", 1 if mutating else 0),
            )
            return int(cur.lastrowid)

    def unfinished_mutations(self, task_id: int) -> list[Step]:
        """Steps that began a change and never recorded the outcome.

        A step in this state is the dangerous case after a crash: the change
        may have happened, or it may not. Peppermint must not guess.
        """
        return [s for s in self.get_steps(task_id) if s.status == "executing"]

    def update_step(self, step_id: int, output: str, status: str = "ok") -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE steps SET output = ?, status = ? WHERE id = ?",
                (output, status, step_id),
            )

    def get_steps(self, task_id: int) -> list[Step]:
        rows = self.connection().execute(
            "SELECT * FROM steps WHERE task_id = ? ORDER BY id ASC", (task_id,)
        ).fetchall()
        out = []
        for r in rows:
            try:
                args = json.loads(r["args"])
            except (ValueError, TypeError):
                args = {}
            out.append(Step(int(r["id"]), int(r["task_id"]), r["tool"], args,
                            r["risk"], r["output"], r["status"], r["ts"]))
        return out

    # --- messages ----------------------------------------------------------

    def add_message(self, task_id: int, role: str, content: dict | str) -> None:
        payload = content if isinstance(content, str) else json.dumps(content)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO messages (task_id, role, content, ts) VALUES (?,?,?,?)",
                (task_id, role, payload, now()),
            )

    def get_messages(self, task_id: int) -> list[dict]:
        rows = self.connection().execute(
            "SELECT role, content FROM messages WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                message = json.loads(r["content"])
                # Older versions stored automatic continuations as user text.
                if message.get("role") == "user" and message.get("content") in (
                    "You described work you have not done. Do not describe. Call the tool now. "
                    "Write text only when every part of the task is complete.",
                    "Continue. Call a tool, or write the final summary.",
                ):
                    message["internal"] = True
                out.append(message)
            except (ValueError, TypeError):
                out.append({"role": r["role"], "content": r["content"]})
        return out

    def clear_messages(self, task_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM messages WHERE task_id = ?", (task_id,))

    # --- confirmations -----------------------------------------------------

    def add_confirmation(self, task_id: int, step_id: int, description: str,
                         reason: str = "", token: str = "", expires_at: str = "",
                         fingerprint: str = "") -> int:
        with self.connection() as conn:
            cur = conn.execute(
                "INSERT INTO confirmations"
                " (task_id, step_id, description, reason, ts, token, expires_at, fingerprint)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (task_id, step_id, description, reason, now(), token, expires_at, fingerprint),
            )
            return int(cur.lastrowid)

    def pending_confirmation(self, task_id: int) -> Confirmation | None:
        row = self.pending_confirmation_row(task_id)
        if row is None:
            return None
        return Confirmation(int(row["id"]), int(row["task_id"]), int(row["step_id"]),
                            row["description"], bool(row["resolved"]), bool(row["approved"]))

    def pending_confirmation_row(self, task_id: int) -> dict | None:
        """The full row, including the token and the target fingerprint."""
        row = self.connection().execute(
            "SELECT * FROM confirmations WHERE task_id = ? AND resolved = 0"
            " ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def resolve_confirmation(self, confirmation_id: int, approved: bool) -> bool:
        """Answer an approval once. Returns False when it was already answered.

        Two windows can show the same question. Only the first answer counts,
        so the update carries its own condition instead of trusting a prior read.
        """
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE confirmations SET resolved = 1, approved = ?"
                " WHERE id = ? AND resolved = 0",
                (1 if approved else 0, confirmation_id),
            )
            return cur.rowcount > 0

    def expired_confirmations(self, before: str) -> list[dict]:
        rows = self.connection().execute(
            "SELECT * FROM confirmations WHERE resolved = 0 AND expires_at != ''"
            " AND expires_at < ?",
            (before,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- undo --------------------------------------------------------------

    def record_undo(self, task_id: int, kind: str, target: str, old_value: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO undo (task_id, kind, target, old_value, ts) VALUES (?,?,?,?,?)",
                (task_id, kind, target, old_value, now()),
            )

    def last_undo(self, limit: int = 20, task_id: int = 0,
                  include_done: bool = False) -> list[dict]:
        query = "SELECT * FROM undo WHERE 1=1"
        params: list = []
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if not include_done:
            query += " AND undone = 0"
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.connection().execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def mark_undone(self, undo_id: int) -> bool:
        """Mark one record as put back. Returns False if it already was.

        The condition lives in the statement, so two windows cannot undo the
        same change twice.
        """
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE undo SET undone = 1 WHERE id = ? AND undone = 0", (undo_id,)
            )
            return cur.rowcount > 0
