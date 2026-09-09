"""Task pages report saved state without loading conversation history."""

import json

import pytest
from gi.repository import Gio

from peppermint.common import dbus_api
from peppermint.common.models import Status
from peppermint.daemon.db import Database
from peppermint.daemon.main import Daemon


@pytest.fixture
def db():
    return Database(":memory:")


def add(db, idea, status=Status.QUEUED):
    task_id = db.add_task(idea)
    db.set_status(task_id, status)
    return task_id


def test_empty_overview_has_every_status_and_no_fabricated_progress(db):
    view = db.task_overview()
    assert view == dict(tasks=[], counts={s.value: 0 for s in Status}, total=0,
                        matched=0, offset=0, limit=40, has_more=False)


def test_pages_cover_all_tasks_without_repeating_ties(db):
    ids = [add(db, f"Task {i}") for i in range(105)]
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET updated_at = '2026-09-08T12:00:00+00:00'")
    seen = []
    for offset in (0, 40, 80):
        page = db.task_overview(offset=offset)
        assert page["total"] == page["matched"] == 105
        assert page["has_more"] is (offset < 80)
        seen.extend(t["id"] for t in page["tasks"])
    assert seen == list(reversed(ids))
    past_end = db.task_overview(offset=150)
    assert not past_end["tasks"] and not past_end["has_more"]
    assert past_end["matched"] == 105


def test_last_update_precedes_id_sorting_and_list_tasks_is_unchanged(db):
    older, newer = add(db, "Older task"), add(db, "Newer task")
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET updated_at = '2026-09-08T12:00:00+00:00' WHERE id = ?", (newer,))
        conn.execute("UPDATE tasks SET updated_at = '2026-09-08T13:00:00+00:00' WHERE id = ?", (older,))
    assert [t["id"] for t in db.task_overview()["tasks"]] == [older, newer]
    assert [t.id for t in db.list_tasks()] == [newer, older]


@pytest.mark.parametrize("name,expected", [
    ("all", set(Status)),
    ("active", {Status.QUEUED, Status.PLANNING, Status.RUNNING}),
    ("waiting", {Status.AWAITING_CONFIRMATION, Status.AWAITING_INPUT}),
    ("finished", {Status.DONE, Status.FAILED, Status.CANCELLED}),
    ("failed", {Status.FAILED}),
])
def test_status_filters_do_not_change_global_counts(db, name, expected):
    for status in Status:
        add(db, "Game " + status.value, status)
    add(db, "Unrelated failure", Status.FAILED)
    view = db.task_overview(name, "GAME")
    assert {t["status"] for t in view["tasks"]} == {s.value for s in expected}
    assert view["matched"] == len(expected)
    assert view["total"] == 9
    assert view["counts"] == {s.value: 2 if s is Status.FAILED else 1 for s in Status}


@pytest.mark.parametrize("query,expected", [
    ("100%", ["A 100% CPU spike"]),
    ("file_name", ["Find file_name"]),
    (r"C:\Games", [r"Read C:\Games"]),
    ("' OR 1=1 --", ["Literal ' OR 1=1 -- string"]),
    ("STRASSE", ["Check Straße"]),
    ("münchen", ["Sync MÜNCHEN"]),
])
def test_search_is_case_insensitive_literal_substring(db, query, expected):
    ideas = ["A 100% CPU spike", "A 1000 CPU spike", "Find file_name", "Find fileXname",
             r"Read C:\Games", "Read C:Games", "Literal ' OR 1=1 -- string",
             "Check Straße", "Sync MÜNCHEN"]
    for idea in ideas:
        add(db, idea)
    view = db.task_overview(query=query)
    assert [t["idea"] for t in view["tasks"]] == expected
    assert view["matched"] == len(expected)
    assert view["total"] == len(ideas)


def test_search_length_and_page_bounds_are_clamped(db):
    wanted = add(db, "x" * 240)
    add(db, "Other")
    view = db.task_overview(query="x" * 240 + "ignored", offset=-20, limit=0)
    assert [t["id"] for t in view["tasks"]] == [wanted]
    assert view["offset"] == 0 and view["limit"] == 1
    assert db.task_overview(limit=1000)["limit"] == 100


@pytest.mark.parametrize("name", ["running", "unknown", "ALL", "", None, []])
def test_invalid_filter_is_rejected(db, name):
    with pytest.raises(ValueError, match="filter"):
        db.task_overview(name)


def test_summaries_include_plans_without_reading_logs_or_messages(db, monkeypatch):
    ids = [add(db, f"Task {i}", Status.AWAITING_INPUT) for i in range(20)]
    plan = [dict(description="Inspect resources", status="pending", kind="inspection")]
    db.set_plan(ids[-1], plan)
    db.add_step(ids[-1], "read_file", {}, "safe", "Private file text")
    db.add_message(ids[-1], "user", dict(role="user", content="Private conversation text"))
    for method in ("get_steps", "get_messages", "get_plan", "pending_confirmation"):
        monkeypatch.setattr(db, method, lambda *_: pytest.fail("Overview loaded per-task details"))
    queries = []
    db.connection().set_trace_callback(queries.append)
    view = db.task_overview(limit=100)
    db.connection().set_trace_callback(None)
    assert len([q for q in queries if q.startswith("SELECT")]) == 3
    assert view["tasks"][0]["plan"] == plan
    assert all(t["messages"] == [] and t["steps"] == [] for t in view["tasks"])
    assert "Private" not in json.dumps(view)


def test_counts_and_page_share_a_read_snapshot(tmp_path):
    path = tmp_path / "overview.db"
    db, writer = Database(path), Database(path)
    first = add(db, "First")
    inserted = False

    def between_queries(statement):
        nonlocal inserted
        if statement.startswith("SELECT COUNT(*) FROM tasks t") and not inserted:
            inserted = True
            add(writer, "Arrived during page read")

    db.connection().set_trace_callback(between_queries)
    view = db.task_overview()
    db.connection().set_trace_callback(None)
    assert inserted
    assert view["total"] == view["matched"] == 1
    assert [t["id"] for t in view["tasks"]] == [first]
    assert db.task_overview()["total"] == 2


def test_overview_does_not_commit_or_rollback_a_callers_transaction(db):
    conn = db.connection()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO tasks (idea,status,created_at,updated_at) VALUES ('Pending write','queued','','')")
    assert db.task_overview()["total"] == 1
    assert conn.in_transaction
    conn.rollback()
    assert db.task_overview()["total"] == 0


def test_overview_bus_signature_and_dispatch_are_read_only(db):
    method = Gio.DBusNodeInfo.new_for_xml(dbus_api.DAEMON_XML).interfaces[0].lookup_method("TaskOverview")
    assert [(a.name, a.signature) for a in method.in_args] == [
        ("status_filter", "s"), ("query", "s"), ("offset", "i"), ("limit", "i"),
    ]
    assert [a.signature for a in method.out_args] == ["s"]
    add(db, "Diagnose game", Status.FAILED)
    daemon = Daemon.__new__(Daemon)
    daemon.db = db
    # A read-only handler needs neither an agent nor a worker queue.
    response = daemon._dispatch("TaskOverview", ("failed", "game", 0, 10))
    view = json.loads(response.unpack()[0])
    assert view["matched"] == 1 and view["tasks"][0]["idea"] == "Diagnose game"
    assert view["limit"] == 10
    with pytest.raises(ValueError, match="filter"):
        daemon._dispatch("TaskOverview", ("unknown", "", 0, 10))
