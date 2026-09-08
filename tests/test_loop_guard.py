"""Peppermint must not repeat a call that does not work.

This comes from a real task. The model asked to run the same install command
29 times. Every one failed the same way, and every one asked the user for
approval. That is what made the approval prompt useless.
"""

from __future__ import annotations

import pytest

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon.agent import Agent, attempt_key
from peppermint.daemon.db import Database
from tests.test_agent import FakeCall, FakeLLM, FakeMessage


@pytest.fixture
def db():
    return Database(":memory:")


def repeat(tool: str, args: dict, times: int):
    return [FakeMessage(tool_calls=[FakeCall(tool, args)]) for _ in range(times)]


def run_with_permissions(agent, task_id):
    result = agent.run(task_id)
    while result.status is Status.AWAITING_CONFIRMATION:
        result = agent.resume_after_confirm(task_id, True)
    return result


# --- the key --------------------------------------------------------------

def test_the_same_call_has_the_same_key():
    assert (attempt_key("run_shell", {"cmd": "ls", "purpose": "x"})
            == attempt_key("run_shell", {"purpose": "x", "cmd": "ls"}))


def test_a_different_argument_is_a_different_call():
    assert (attempt_key("run_shell", {"cmd": "ls"})
            != attempt_key("run_shell", {"cmd": "df"}))


# --- the guard ------------------------------------------------------------

def test_a_repeated_call_is_blocked_not_run(db):
    llm = FakeLLM(repeat("system_info", {"topic": "os"}, 5)
                  + [FakeMessage(content="I stopped repeating myself.")])
    agent = Agent(db, llm)
    task_id = db.add_task("do the same thing over and over")

    result = run_with_permissions(agent, task_id)

    steps = db.get_steps(task_id)
    ran = [s for s in steps if s.status == "ok"]
    blocked = [s for s in steps if s.status == "blocked"]
    assert len(ran) == config.MAX_SAME_CALL, f"ran {len(ran)} times, expected {config.MAX_SAME_CALL}"
    assert blocked, "the extra calls must be recorded as blocked"
    assert result.status in (Status.DONE, Status.FAILED)


def test_the_model_is_told_what_happened_last_time(db):
    llm = FakeLLM(repeat("system_info", {"topic": "os"}, 4)
                  + [FakeMessage(content="Fine, I will stop.")])
    agent = Agent(db, llm)
    task_id = db.add_task("repeat")
    run_with_permissions(agent, task_id)

    warning = [s for s in db.get_steps(task_id) if s.status == "blocked"][0].output
    assert "already made this exact call" in warning
    assert "Do something different" in warning


def test_a_stubborn_loop_stops_the_task(db):
    llm = FakeLLM(repeat("system_info", {"topic": "os"}, 12))
    agent = Agent(db, llm)
    task_id = db.add_task("never give up")

    result = run_with_permissions(agent, task_id)

    assert result.status is Status.FAILED
    assert "same action" in result.text
    assert "Nothing was run again" in result.text


def test_a_repeated_risky_call_never_asks_twice(db, home_tmp):
    """The real complaint: 29 approval prompts for one failing command."""
    victim = home_tmp / "x.txt"
    victim.write_text("x")
    calls = repeat("run_shell", {"cmd": f"rm {victim}", "purpose": "remove"}, 6)
    llm = FakeLLM(calls + [FakeMessage(content="I gave up.")])
    agent = Agent(db, llm)
    task_id = db.add_task("remove it")

    # Ask, refuse, and let the model try the very same thing again.
    agent.run(task_id)
    for _ in range(4):
        if db.pending_confirmation(task_id) is None:
            break
        agent.resume_after_confirm(task_id, approved=False)

    asked = [s for s in db.get_steps(task_id) if s.risk == "risky"]
    assert len(asked) <= config.MAX_SAME_CALL, \
        f"Peppermint asked {len(asked)} times for the same action"
    assert victim.exists()


def test_different_calls_are_not_blocked(db):
    """The guard must not stop real work that touches many files."""
    llm = FakeLLM([FakeMessage(tool_calls=[FakeCall("system_info", {"topic": t})])
                   for t in ("os", "disk", "memory", "cpu", "network")]
                  + [FakeMessage(content="Here are the facts.")])
    agent = Agent(db, llm)
    task_id = db.add_task("tell me about this computer")

    result = run_with_permissions(agent, task_id)

    assert result.status is Status.DONE
    assert len([s for s in db.get_steps(task_id) if s.status == "ok"]) == 5
    assert not [s for s in db.get_steps(task_id) if s.status == "blocked"]


def test_new_user_turn_can_repeat_a_successful_inspection(db):
    agent = Agent(db, FakeLLM(repeat("system_info", {"topic": "os"}, 2)
                              + [FakeMessage(content="Done once.")]))
    task_id = db.add_task("first go")
    run_with_permissions(agent, task_id)
    agent.llm = FakeLLM(repeat("system_info", {"topic": "os"}, 1)
                        + [FakeMessage(content="Checked again.")])
    result = agent.follow_up(task_id, "check again")
    assert result.status is Status.AWAITING_CONFIRMATION
    result = agent.resume_after_confirm(task_id, True)
    assert result.status is Status.DONE
    assert len([s for s in db.get_steps(task_id) if s.status == "ok"]) == 3


def test_shell_purpose_cannot_evade_loop_guard():
    assert attempt_key('run_shell', {'cmd': 'false', 'purpose': 'first'}) == attempt_key(
        'run_shell', {'cmd': 'false', 'purpose': 'second'})


def test_approval_does_not_reset_model_call_budget(db, monkeypatch):
    monkeypatch.setattr(config, 'MAX_ITERATIONS', 2)
    agent = Agent(db, FakeLLM([
        FakeMessage(tool_calls=[FakeCall('system_info', {'topic': 'os'})]),
        FakeMessage(tool_calls=[FakeCall('system_info', {'topic': 'memory'})]),
        FakeMessage(content='Should not get this far.'),
    ]))
    task_id = db.add_task('Gather facts')
    assert agent.run(task_id).status is Status.AWAITING_CONFIRMATION
    assert agent.resume_after_confirm(task_id, True).status is Status.AWAITING_CONFIRMATION
    assert agent.resume_after_confirm(task_id, True).status is Status.FAILED
    assert len(agent.llm.calls) == 2
