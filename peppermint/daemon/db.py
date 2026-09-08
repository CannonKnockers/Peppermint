"""SQLite storage for tasks, steps, messages, confirmations, and undo data.

The daemon runs a GLib main loop and one worker thread. Both touch the
database, so every connection uses WAL mode and a busy timeout.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from peppermint import config
from peppermint.common.models import Confirmation, Status, Step, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    idea        TEXT    NOT NULL,
    status      TEXT    NOT NULL,
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

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_confirm_task ON confirmations(task_id, resolved);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


SCHEMA_VERSION = 3

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
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
            # A database made before this table existed may already hold the
            # newest columns, because the CREATE statements above are current.
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(confirmations)")}
            version = SCHEMA_VERSION if "token" in columns else 1
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
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
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
        return task

    def list_tasks(self, limit: int = 50) -> list[Task]:
        conn = self.connection()
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        tasks = [self._row_to_task(r) for r in rows]
        for task in tasks:
            task.plan = self.get_plan(task.id)
            if Status(task.status).needs_user:
                task.pending = self.pending_confirmation(task.id)
        return tasks

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

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=int(row["id"]),
            idea=row["idea"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=row["result"],
            error=row["error"],
            question=row["question"],
        )

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
