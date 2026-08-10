"""The agent loop, driven by a scripted fake model. No GPU is needed."""

from __future__ import annotations

import pytest

from peppermint.common.models import Status
from peppermint.daemon import tools
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database


class FakeCall:
    def __init__(self, name, arguments):
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls


class FakeLLM:
    """Returns the scripted replies in order. Records what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.model = "fake"

    def chat(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self.replies:
            return FakeMessage(content="I have nothing more to do.")
        return self.replies.pop(0)


@pytest.fixture
def db():
    return Database(":memory:")


def test_simple_answer_with_no_tool(db):
    llm = FakeLLM([FakeMessage(content="Your theme is Mint-Y-Dark-Aqua.")])
    agent = Agent(db, llm)
    task_id = db.add_task("what theme am I using")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    assert "Mint-Y-Dark-Aqua" in result.text
    assert db.get_task(task_id).status == Status.DONE.value


def test_safe_tool_runs_without_approval(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("system_info", {"topic": "memory"})]),
        FakeMessage(content="You have enough memory."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("how much memory do I have")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    steps = db.get_steps(task_id)
    assert len(steps) == 1
    assert steps[0].tool == "system_info"
    assert steps[0].status == "ok"


def test_risky_tool_stops_and_waits(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": "rm -rf /tmp/x", "purpose": "clean up"})]),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("delete the temp folder")

    result = agent.run(task_id)

    assert result.status is Status.AWAITING_CONFIRMATION
    task = db.get_task(task_id)
    assert task.status == Status.AWAITING_CONFIRMATION.value
    assert task.pending is not None
    assert "rm -rf /tmp/x" in task.pending.description


def test_denied_action_tells_the_model_and_continues(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": "rm -rf /tmp/x", "purpose": "clean"})]),
        FakeMessage(content="I did not delete anything, because you refused."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("delete the temp folder")
    agent.run(task_id)

    result = agent.resume_after_confirm(task_id, approved=False)

    assert result.status is Status.DONE
    steps = db.get_steps(task_id)
    assert steps[0].status == "denied"
    last_call = llm.calls[-1]
    assert any("did not allow" in str(m.get("content", "")) for m in last_call)


def test_approved_action_runs(db, tmp_path):
    victim = tmp_path / "target.txt"
    victim.write_text("hello")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": f"rm {victim}", "purpose": "remove it"})]),
        FakeMessage(content="I removed the file."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("remove that file")
    agent.run(task_id)

    result = agent.resume_after_confirm(task_id, approved=True)

    assert result.status is Status.DONE
    assert not victim.exists()


def test_approved_action_makes_one_step_not_two(db, tmp_path):
    """The approved step updates in place. It must not appear twice."""
    victim = tmp_path / "once.txt"
    victim.write_text("x")
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("run_shell", {"cmd": f"rm {victim}", "purpose": "remove"})]),
        FakeMessage(content="Removed."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("remove it")
    agent.run(task_id)
    agent.resume_after_confirm(task_id, approved=True)

    steps = db.get_steps(task_id)
    assert len(steps) == 1, f"expected one step, got {[s.tool for s in steps]}"
    assert steps[0].status == "ok"
    assert steps[0].risk == "risky"


def test_ask_user_pauses_the_task(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("ask_user", {"question": "Which folder do you mean?"})]),
        FakeMessage(content="Done, I used the Documents folder."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("sort my folder")

    result = agent.run(task_id)
    assert result.status is Status.AWAITING_INPUT
    assert "Which folder" in result.text
    assert db.get_task(task_id).question == "Which folder do you mean?"

    result = agent.resume_after_answer(task_id, "Documents")
    assert result.status is Status.DONE
    assert any("Documents" in str(m.get("content", "")) for m in llm.calls[-1])


def test_unknown_tool_is_repaired_not_fatal(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("make_coffee", {})]),
        FakeMessage(tool_calls=[FakeCall("system_info", {"topic": "os"})]),
        FakeMessage(content="You use Linux Mint."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("what system is this")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    steps = db.get_steps(task_id)
    assert steps[0].status == "error"
    assert "no tool named" in steps[0].output


def test_missing_argument_is_repaired(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("read_file", {})]),
        FakeMessage(content="I need a path."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("read something")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    assert "needs these arguments" in db.get_steps(task_id)[0].output


def test_too_many_bad_calls_fail_the_task(db, monkeypatch):
    from peppermint import config

    monkeypatch.setattr(config, "MAX_REPAIRS", 2)
    # Each call is different, so the repair limit is what stops this, not the
    # guard against a repeated call.
    llm = FakeLLM([FakeMessage(tool_calls=[FakeCall(f"nope{i}", {})]) for i in range(6)])
    agent = Agent(db, llm)
    task_id = db.add_task("do something impossible")

    result = agent.run(task_id)

    assert result.status is Status.FAILED
    assert "could not use the tools" in result.text


def test_iteration_limit_fails_cleanly(db, monkeypatch):
    from peppermint import config

    monkeypatch.setattr(config, "MAX_ITERATIONS", 3)
    llm = FakeLLM([FakeMessage(tool_calls=[FakeCall("system_info", {"topic": "os"})]) for _ in range(10)])
    agent = Agent(db, llm)
    task_id = db.add_task("loop for ever")

    result = agent.run(task_id)

    assert result.status is Status.FAILED
    assert "did not finish" in result.text


def test_empty_reply_gets_one_nudge_then_fails(db):
    llm = FakeLLM([FakeMessage(content=""), FakeMessage(content="")])
    agent = Agent(db, llm)
    task_id = db.add_task("say nothing")

    result = agent.run(task_id)

    assert result.status is Status.FAILED
    assert "without an answer" in result.text


def test_a_promise_is_not_an_answer(db):
    """The model often says what it will do. Peppermint must push it on."""
    llm = FakeLLM([
        FakeMessage(content="Now, I'll create the folders and move the files."),
        FakeMessage(tool_calls=[FakeCall("list_dir", {"path": "/tmp"})]),
        FakeMessage(content="I made three folders and moved ten files."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("sort my files")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    assert "made three folders" in result.text
    assert len(db.get_steps(task_id)) == 1


@pytest.mark.parametrize("text", [
    "I'll create the folders now.",
    "I will move the files next.",
    "Let me check the directory first.",
    "Next, I need to make the folders.",
    "First, I should read the settings.",
    "I am going to install the package.",
])
def test_promise_texts_are_detected(text):
    from peppermint.daemon.agent import promises_more

    assert promises_more(text)


@pytest.mark.parametrize("text", [
    "I moved ten files into three folders.",
    "Your theme is Mint-Y-Dark-Aqua.",
    "The main drive has 56 GB free.",
    "I could not finish, because the folder does not exist.",
    "I created a shortcut for the terminal.",
])
def test_real_answers_are_not_treated_as_promises(text):
    from peppermint.daemon.agent import promises_more

    assert not promises_more(text)


def test_the_nudge_gives_up_and_accepts_the_text(db, monkeypatch):
    """A model that only ever promises must not loop for ever."""
    from peppermint import config

    monkeypatch.setattr(config, "MAX_CONTINUE_NUDGES", 2)
    llm = FakeLLM([FakeMessage(content="I'll do it now.") for _ in range(8)])
    agent = Agent(db, llm)
    task_id = db.add_task("do a thing")

    result = agent.run(task_id)

    assert result.status is Status.DONE
    assert llm.calls and len(llm.calls) == 3  # first try plus two nudges


def test_history_survives_and_grows(db):
    llm = FakeLLM([FakeMessage(content="First answer."), FakeMessage(content="Second answer.")])
    agent = Agent(db, llm)
    task_id = db.add_task("hello")

    agent.run(task_id)
    first = len(db.get_messages(task_id))
    agent.follow_up(task_id, "and now this")

    assert len(db.get_messages(task_id)) > first
    assert db.get_task(task_id).result == "Second answer."


def test_system_prompt_has_the_real_home_path():
    from peppermint.daemon.agent import HOME, system_prompt

    prompt = system_prompt()
    assert HOME in prompt
    assert "/home/user" not in prompt


def test_every_tool_has_a_valid_schema():
    for schema in tools.schemas():
        function = schema["function"]
        assert function["name"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
