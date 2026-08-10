"""Storage must keep tasks safe across a restart."""

import pytest

from peppermint.common.models import Status
from peppermint.daemon.db import Database


@pytest.fixture
def db():
    return Database(":memory:")


def test_add_and_read_a_task(db):
    task_id = db.add_task("sort my files")
    task = db.get_task(task_id)
    assert task.idea == "sort my files"
    assert task.status == Status.QUEUED.value


def test_status_change_keeps_the_result(db):
    task_id = db.add_task("do a thing")
    db.set_status(task_id, Status.DONE, result="I did the thing.")
    task = db.get_task(task_id)
    assert task.status == Status.DONE.value
    assert task.result == "I did the thing."


def test_steps_keep_their_order(db):
    task_id = db.add_task("many steps")
    db.add_step(task_id, "list_dir", {"path": "~"}, "safe", "a\nb")
    db.add_step(task_id, "read_file", {"path": "~/a"}, "safe", "text")
    steps = db.get_steps(task_id)
    assert [s.tool for s in steps] == ["list_dir", "read_file"]
    assert steps[0].args == {"path": "~"}


def test_recover_puts_reading_tasks_back_in_the_queue(db):
    running = db.add_task("was running")
    planning = db.add_task("was planning")
    waiting = db.add_task("waits for the user")
    db.set_status(running, Status.RUNNING)
    db.set_status(planning, Status.PLANNING)
    db.set_status(waiting, Status.AWAITING_CONFIRMATION)
    db.add_step(running, "list_dir", {"path": "~"}, "safe", "a\nb", "ok")

    result = db.recover()

    assert set(result["resumed"]) == {running, planning}
    assert result["halted"] == []
    assert db.get_task(running).status == Status.QUEUED.value
    assert db.get_task(waiting).status == Status.AWAITING_CONFIRMATION.value


def test_recover_never_repeats_a_change_it_cannot_check(db):
    """A crash during a change must not be replayed. It might already be done."""
    task_id = db.add_task("delete a file")
    db.set_status(task_id, Status.RUNNING)
    db.add_step(task_id, "delete_file", {"path": "/home/jesse/x"}, "risky",
                "Peppermint is doing this now.", "executing", mutating=True)

    result = db.recover()

    assert result["resumed"] == []
    assert result["halted"] == [task_id]
    task = db.get_task(task_id)
    assert task.status == Status.FAILED.value
    assert "does not know whether that change finished" in task.error
    assert task.steps[0].status == "interrupted"


def test_a_finished_change_does_not_block_recovery(db):
    """Only an unfinished change is ambiguous. A recorded one is not."""
    task_id = db.add_task("delete a file")
    db.set_status(task_id, Status.RUNNING)
    step = db.add_step(task_id, "delete_file", {"path": "/home/jesse/x"}, "risky",
                       "", "executing", mutating=True)
    db.update_step(step, "Moved to the trash.", "ok")

    result = db.recover()

    assert result["resumed"] == [task_id]
    assert result["halted"] == []


# --- migrations -----------------------------------------------------------

def test_a_fresh_database_is_at_the_current_version(db):
    from peppermint.daemon.db import SCHEMA_VERSION

    assert db.current_version() == SCHEMA_VERSION


def test_migrating_twice_changes_nothing(db):
    first = db.migrate()
    assert db.migrate() == first


def test_an_old_database_gains_the_new_columns(tmp_path):
    """A database made by version 1 must keep its data and gain the columns."""
    import sqlite3

    from peppermint.daemon.db import Database

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, idea TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            question TEXT NOT NULL DEFAULT '');
        CREATE TABLE steps (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            tool TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}', risk TEXT NOT NULL DEFAULT 'safe',
            output TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok', ts TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL);
        CREATE TABLE confirmations (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            step_id INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', resolved INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0, ts TEXT NOT NULL);
        CREATE TABLE undo (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            kind TEXT NOT NULL, target TEXT NOT NULL, old_value TEXT NOT NULL DEFAULT '',
            ts TEXT NOT NULL);
        INSERT INTO tasks (idea, status, created_at, updated_at)
            VALUES ('an old task', 'done', '2026-01-01', '2026-01-01');
    """)
    old.commit()
    old.close()

    upgraded = Database(path)

    from peppermint.daemon.db import SCHEMA_VERSION

    assert upgraded.current_version() == SCHEMA_VERSION
    assert upgraded.get_task(1).idea == "an old task", "the old data must survive"
    upgraded.add_confirmation(1, 0, "x", token="abc", expires_at="2030-01-01", fingerprint="{}")
    assert upgraded.pending_confirmation_row(1)["token"] == "abc"


def test_the_integrity_check_passes(db):
    assert db.integrity_ok()


# --- one answer only ------------------------------------------------------

def test_only_the_first_answer_to_an_approval_counts(db):
    """Two windows can show the same question. The second click must do nothing."""
    task_id = db.add_task("risky thing")
    step_id = db.add_step(task_id, "run_shell", {"cmd": "rm x"}, "risky", "", "pending")
    confirm_id = db.add_confirmation(task_id, step_id, "Run: rm x")

    assert db.resolve_confirmation(confirm_id, approved=True) is True
    assert db.resolve_confirmation(confirm_id, approved=False) is False, \
        "the second answer must not overwrite the first"
    row = db.pending_confirmation_row(task_id)
    assert row is None


def test_next_queued_takes_the_oldest_task(db):
    first = db.add_task("first")
    db.add_task("second")
    assert db.next_queued().id == first


def test_confirmation_round_trip(db):
    task_id = db.add_task("risky thing")
    step_id = db.add_step(task_id, "run_shell", {"cmd": "rm x"}, "risky", "", "pending")
    confirm_id = db.add_confirmation(task_id, step_id, "Run: rm x", "it deletes data")

    pending = db.pending_confirmation(task_id)
    assert pending.description == "Run: rm x"

    db.resolve_confirmation(confirm_id, approved=True)
    assert db.pending_confirmation(task_id) is None


def test_messages_keep_tool_calls(db):
    task_id = db.add_task("with tools")
    db.add_message(task_id, "assistant", {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "list_dir", "arguments": {"path": "~"}}}],
    })
    messages = db.get_messages(task_id)
    assert messages[0]["tool_calls"][0]["function"]["name"] == "list_dir"


def test_undo_records_the_old_value(db):
    task_id = db.add_task("change the theme")
    db.record_undo(task_id, "gsettings", "org.cinnamon.theme name", "'Mint-Y-Dark-Aqua'")
    rows = db.last_undo()
    assert rows[0]["old_value"] == "'Mint-Y-Dark-Aqua'"


def test_list_tasks_shows_the_newest_first(db):
    db.add_task("old")
    newest = db.add_task("new")
    assert db.list_tasks()[0].id == newest
