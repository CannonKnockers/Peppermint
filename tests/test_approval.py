"""An approval authorises one exact action, once, for a short time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace

import pytest

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon import approval
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database
from tests.test_agent import FakeCall, FakeLLM, FakeMessage


@pytest.fixture
def db():
    return Database(":memory:")


# --- tokens ---------------------------------------------------------------

def test_the_same_call_always_makes_the_same_token():
    a = approval.make_token(1, "delete_file", {"path": "/home/jesse/x"})
    b = approval.make_token(1, "delete_file", {"path": "/home/jesse/x"})
    assert a == b


def test_key_order_does_not_change_the_token():
    a = approval.make_token(1, "move_file", {"src": "a", "dst": "b"})
    b = approval.make_token(1, "move_file", {"dst": "b", "src": "a"})
    assert a == b


def test_a_different_argument_makes_a_different_token():
    a = approval.make_token(1, "delete_file", {"path": "/home/jesse/keep"})
    b = approval.make_token(1, "delete_file", {"path": "/home/jesse/other"})
    assert a != b


def test_a_different_tool_makes_a_different_token():
    assert (approval.make_token(1, "delete_file", {"path": "x"})
            != approval.make_token(1, "write_file", {"path": "x"}))


def test_a_different_task_makes_a_different_token():
    """An approval given in one task cannot authorise another task."""
    assert (approval.make_token(1, "delete_file", {"path": "x"})
            != approval.make_token(2, "delete_file", {"path": "x"}))


def test_a_new_policy_version_invalidates_old_tokens(monkeypatch):
    from peppermint.daemon import safety

    before = approval.make_token(1, "delete_file", {"path": "x"})
    monkeypatch.setattr(safety, "POLICY_VERSION", safety.POLICY_VERSION + 1)
    assert approval.make_token(1, "delete_file", {"path": "x"}) != before


# --- expiry ---------------------------------------------------------------

def test_a_fresh_approval_is_not_expired():
    assert not approval.is_expired(approval.expiry_time())


def test_an_old_approval_is_expired():
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert approval.is_expired(past)


def test_unreadable_expiry_counts_as_expired():
    assert approval.is_expired("not a date")


def test_an_empty_expiry_does_not_expire():
    """Rows written before expiry existed must still work."""
    assert not approval.is_expired("")


# --- target fingerprints --------------------------------------------------

def test_an_absent_file_differs_from_an_empty_file(home_tmp):
    """These two states look alike in a size check. They are not the same."""
    path = home_tmp / "x.txt"
    absent = approval.target_fingerprint({"path": str(path)})
    path.write_text("")
    empty = approval.target_fingerprint({"path": str(path)})
    assert absent != empty
    assert "absent" in absent
    assert "absent" not in empty


def test_replacing_the_file_changes_the_fingerprint(home_tmp):
    path = home_tmp / "x.txt"
    path.write_text("first")
    before = approval.target_fingerprint({"path": str(path)})
    path.unlink()
    path.write_text("second")
    assert approval.compare_targets(before, approval.target_fingerprint({"path": str(path)}))


def test_turning_a_file_into_a_link_is_noticed(home_tmp):
    path = home_tmp / "x.txt"
    path.write_text("real")
    before = approval.target_fingerprint({"path": str(path)})
    path.unlink()
    path.symlink_to("/etc/hosts")
    change = approval.compare_targets(before, approval.target_fingerprint({"path": str(path)}))
    assert change, "a file that became a link must be reported"


def test_an_unchanged_target_reports_nothing(home_tmp):
    path = home_tmp / "x.txt"
    path.write_text("same")
    before = approval.target_fingerprint({"path": str(path)})
    assert approval.compare_targets(before, approval.target_fingerprint({"path": str(path)})) == ""


@pytest.mark.parametrize("argument", ["path", "log_path"])
def test_same_size_rewrite_within_one_second_invalidates_approval(home_tmp, argument):
    path = home_tmp / "steam-480.log"
    path.write_text("original")
    second = path.stat().st_mtime_ns // 1_000_000_000 * 1_000_000_000
    os.utime(path, ns=(second, second + 100_000_000))
    args = {argument: str(path)}
    row = make_row(1, "steam_game_diagnostics", args)
    before = path.stat()
    path.write_text("replaced")
    os.utime(path, ns=(second, second + 200_000_000))
    after = path.stat()
    assert before.st_size == after.st_size
    assert int(before.st_mtime) == int(after.st_mtime)
    assert before.st_mtime_ns != after.st_mtime_ns
    with pytest.raises(approval.ApprovalError, match="target changed"):
        approval.verify(row, "steam_game_diagnostics", args, 1)


def test_restored_mtime_still_notices_changed_ctime(home_tmp, monkeypatch):
    path = home_tmp / "notes.txt"
    path.write_text("original")
    args = {"path": str(path)}
    row = make_row(1, "delete_file", args)
    original_stat = os.lstat
    info = original_stat(path)

    def changed_stat(target, *args, **kwargs):
        measured = original_stat(target, *args, **kwargs)
        if os.fspath(target) != str(path):
            return measured
        # A deterministic filesystem sample of a same-size rewrite followed by
        # restoring mtime. Only ctime differs, including its subsecond portion.
        values = {name: getattr(info, name) for name in (
            "st_mode", "st_size", "st_ino", "st_dev", "st_mtime", "st_mtime_ns", "st_ctime_ns")}
        values["st_ctime_ns"] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(approval.os, "lstat", changed_stat)
    with pytest.raises(approval.ApprovalError, match="target changed"):
        approval.verify(row, "delete_file", args, 1)


def test_reading_an_unchanged_log_does_not_invalidate_approval(home_tmp):
    path = home_tmp / "steam-480.log"
    path.write_text("same launch output")
    args = {"app_id": "480", "log_path": str(path)}
    row = make_row(1, "steam_game_diagnostics", args)
    assert path.read_text() == "same launch output"
    approval.verify(row, "steam_game_diagnostics", args, 1)


@pytest.mark.parametrize("exists", [False, True])
def test_legacy_target_fingerprint_requires_fresh_approval(home_tmp, exists):
    path = home_tmp / "steam-480.log"
    if exists:
        path.write_text("same")
    args = {"log_path": str(path)}
    row = make_row(1, "steam_game_diagnostics", args)
    legacy = json.loads(row["fingerprint"])
    target = legacy["log_path"]
    target.pop("fingerprint_version")
    if exists:
        target["mtime"] = target.pop("mtime_ns") // 1_000_000_000
        target.pop("ctime_ns")
        target.pop("device")
    row["fingerprint"] = json.dumps(legacy)
    with pytest.raises(approval.ApprovalError, match="older target checks; request a fresh approval"):
        approval.verify(row, "steam_game_diagnostics", args, 1)
    # Verification neither rewrites the stored metadata nor changes the target.
    assert row["fingerprint"] == json.dumps(legacy)
    assert path.exists() == exists
    if exists:
        assert path.read_text() == "same"


def test_approval_with_no_filesystem_targets_needs_no_metadata_upgrade():
    args = {"app_id": "480"}
    row = make_row(1, "steam_game_diagnostics", args, fingerprint="{}")
    approval.verify(row, "steam_game_diagnostics", args, 1)


@pytest.mark.parametrize("fingerprint", ['[]', '{"path":null}', '{"path":"old"}'])
def test_malformed_stored_fingerprint_is_refused(home_tmp, fingerprint):
    args = {"path": str(home_tmp / "notes.txt")}
    row = make_row(1, "delete_file", args, fingerprint=fingerprint)
    with pytest.raises(approval.ApprovalError, match="cannot read"):
        approval.verify(row, "delete_file", args, 1)


# --- verification ---------------------------------------------------------

def make_row(task_id, tool, args, **overrides):
    row = {
        "token": approval.make_token(task_id, tool, args),
        "expires_at": approval.expiry_time(),
        "fingerprint": approval.target_fingerprint(args),
    }
    row.update(overrides)
    return row


def test_a_matching_approval_passes(home_tmp):
    args = {"path": str(home_tmp / "x.txt")}
    approval.verify(make_row(1, "delete_file", args), "delete_file", args, 1)


def test_a_changed_argument_is_refused(home_tmp):
    asked = {"path": str(home_tmp / "asked.txt")}
    row = make_row(1, "delete_file", asked)
    with pytest.raises(approval.ApprovalError, match="changed after you approved"):
        approval.verify(row, "delete_file", {"path": str(home_tmp / "other.txt")}, 1)


def test_a_changed_tool_is_refused(home_tmp):
    args = {"path": str(home_tmp / "x.txt")}
    with pytest.raises(approval.ApprovalError):
        approval.verify(make_row(1, "delete_file", args), "write_file", args, 1)


def test_an_approval_from_another_task_is_refused(home_tmp):
    args = {"path": str(home_tmp / "x.txt")}
    with pytest.raises(approval.ApprovalError):
        approval.verify(make_row(1, "delete_file", args), "delete_file", args, 99)


def test_a_missing_token_is_refused(home_tmp):
    args = {"path": str(home_tmp / "x.txt")}
    with pytest.raises(approval.ApprovalError, match="no token"):
        approval.verify(make_row(1, "delete_file", args, token=""), "delete_file", args, 1)


def test_an_expired_approval_is_refused(home_tmp):
    args = {"path": str(home_tmp / "x.txt")}
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    row = make_row(1, "delete_file", args, expires_at=past)
    with pytest.raises(approval.ApprovalError, match="minutes old"):
        approval.verify(row, "delete_file", args, 1)


def test_a_target_swapped_after_approval_is_refused(home_tmp):
    """The classic race: approve one file, then the file becomes a link."""
    path = home_tmp / "notes.txt"
    path.write_text("mine")
    args = {"path": str(path)}
    row = make_row(1, "delete_file", args)

    path.unlink()
    path.symlink_to("/etc/hosts")

    with pytest.raises(approval.ApprovalError, match="target changed"):
        approval.verify(row, "delete_file", args, 1)


# --- the agent uses all of it --------------------------------------------

def test_an_approved_action_runs_when_nothing_changed(db, home_tmp):
    victim = home_tmp / "gone.txt"
    victim.write_text("x")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": f"rm {victim}", "purpose": "remove"})]),
        FakeMessage(content="Removed."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("remove it")
    agent.run(task_id)

    result = agent.resume_after_confirm(task_id, approved=True)

    assert result.status is Status.DONE
    assert not victim.exists()


def test_the_agent_refuses_an_expired_approval(db, home_tmp, monkeypatch):
    victim = home_tmp / "survivor.txt"
    victim.write_text("x")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": f"rm {victim}", "purpose": "remove"})]),
        FakeMessage(content="I did not remove it."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("remove it")
    monkeypatch.setattr(config, "APPROVAL_TTL_S", -1)  # every approval is already stale
    agent.run(task_id)

    agent.resume_after_confirm(task_id, approved=True)

    assert victim.exists(), "an expired approval must not delete anything"
    assert db.get_steps(task_id)[0].status == "refused"


def test_the_agent_refuses_when_the_target_was_swapped(db, home_tmp):
    victim = home_tmp / "swap.txt"
    victim.write_text("mine")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("delete_file", {"path": str(victim)})]),
        FakeMessage(content="I did not remove it."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("delete it")
    agent.run(task_id)

    # Between the question and the click, the file becomes a link elsewhere.
    victim.unlink()
    victim.symlink_to("/etc/hosts")

    agent.resume_after_confirm(task_id, approved=True)

    assert victim.is_symlink(), "the link must still be there"
    step = db.get_steps(task_id)[0]
    assert step.status == "refused"
    assert "target changed" in step.output


def test_a_second_click_does_nothing(db, home_tmp):
    victim = home_tmp / "twice.txt"
    victim.write_text("x")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("delete_file", {"path": str(victim)})]),
        FakeMessage(content="Done."),
        FakeMessage(content="Nothing more to do."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("delete it")
    agent.run(task_id)

    agent.resume_after_confirm(task_id, approved=True)
    steps_after_first = len(db.get_steps(task_id))
    agent.resume_after_confirm(task_id, approved=True)

    assert len(db.get_steps(task_id)) == steps_after_first, \
        "the second click must not add another action"


def test_the_step_records_that_it_started_before_it_acts(db, home_tmp):
    """Without this record, a crash mid-change would be invisible."""
    seen = {}
    victim = home_tmp / "mark.txt"
    victim.write_text("x")

    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("delete_file", {"path": str(victim)})]),
        FakeMessage(content="Done."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("delete it")
    agent.run(task_id)

    original = agent.db.update_step

    def spy(step_id, output, status="ok"):
        seen.setdefault("first", status)
        return original(step_id, output, status)

    agent.db.update_step = spy
    agent.resume_after_confirm(task_id, approved=True)

    assert seen["first"] == "executing", "the step must be marked before the change runs"
