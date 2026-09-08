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


def test_read_only_tool_waits_for_approval(db):
    llm = FakeLLM([
        FakeMessage(tool_calls=[FakeCall("system_info", {"topic": "memory"})]),
        FakeMessage(content="You have enough memory."),
    ])
    agent = Agent(db, llm)
    task_id = db.add_task("how much memory do I have")

    result = agent.run(task_id)

    assert result.status is Status.AWAITING_CONFIRMATION
    assert db.get_steps(task_id)[0].status == "pending"
    result = agent.resume_after_confirm(task_id, True)
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
    assert result.status is Status.AWAITING_CONFIRMATION
    result = agent.resume_after_confirm(task_id, True)

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
    llm = FakeLLM([FakeMessage(tool_calls=[FakeCall(f"unknown{i}", {})]) for i in range(10)])
    monkeypatch.setattr(config, "MAX_REPAIRS", 20)
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


@pytest.mark.parametrize("answer", [
    "The task is complete. Let me know if you need further assistance!",
    "The three largest files are a, b, and c. I will help if you need anything else.",
    "I'll create the folders now.",
])
def test_text_answer_finishes_without_automatic_continuation(db, answer):
    llm = FakeLLM([FakeMessage(content=answer)])
    agent = Agent(db, llm)
    task_id = db.add_task("list the top three files")
    assert agent.run(task_id).status is Status.DONE
    assert len(llm.calls) == 1
    assert db.get_task(task_id).result == answer


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


@pytest.mark.parametrize('name,args', [
    ('run_shell', {'cmd': 'pwd', 'purpose': 'Inspect location'}),
    ('read_file', {'path': '/tmp/example'}),
    ('open_url', {'url': 'https://example.com'}),
    ('system_info', {'topic': 'memory'}),
    ('make_dir', {'path': '/tmp/example'}),
])
def test_computer_tools_do_not_execute_before_permission(db, monkeypatch, name, args):
    from peppermint.daemon.tools.registry import REGISTRY
    import functools
    original = REGISTRY[name].func

    @functools.wraps(original)
    def forbidden(*args, **kwargs):
        pytest.fail('Tool executed without permission')

    monkeypatch.setattr(REGISTRY[name], 'func', forbidden)
    agent = Agent(db, FakeLLM([FakeMessage(tool_calls=[FakeCall(name, args)])]))
    task_id = db.add_task('Do some work')
    assert agent.run(task_id).status is Status.AWAITING_CONFIRMATION
    assert agent.resume_after_confirm(task_id, False).status is Status.DONE


def test_choices_and_conversation_survive_reopening(tmp_path):
    path = tmp_path / 'history.db'
    db = Database(path)
    task_id = db.add_task('Help me organize files')
    agent = Agent(db, FakeLLM([
        FakeMessage(tool_calls=[FakeCall('ask_user', {
            'question': 'How should I organize them?',
            'options': ['By type', 'By date'],
        })]),
        FakeMessage(content='You chose type.'),
        FakeMessage(content='We can do that next.'),
    ]))
    assert agent.run(task_id).status is Status.AWAITING_INPUT
    assert db.get_steps(task_id)[0].args['options'] == ['By type', 'By date']
    agent.resume_after_answer(task_id, 'By type')
    agent.follow_up(task_id, 'What about photos?')
    messages = Database(path).get_task(task_id).to_dict()['messages']
    assert [m['content'] for m in messages if m['role'] == 'user'] == [
        'Help me organize files', 'By type', 'What about photos?']
    assert [m['content'] for m in messages if m['role'] == 'assistant'] == [
        'You chose type.', 'We can do that next.']


def test_internal_nudges_are_not_shown_as_user_prompts(db):
    task_id = db.add_task('Hello')
    agent = Agent(db, FakeLLM([FakeMessage(), FakeMessage(content='Hello!')]))
    agent.run(task_id)
    assert [m['content'] for m in db.get_task(task_id).messages if m['role'] == 'user'] == ['Hello']


def test_followup_is_visible_before_model_starts(db):
    llm = FakeLLM([FakeMessage(content="First answer."), FakeMessage(content="Second answer.")])
    agent = Agent(db, llm)
    task_id = db.add_task("First prompt")
    agent.run(task_id)
    agent.queue_follow_up(task_id, "Second prompt")
    task = db.get_task(task_id)
    assert task.status == "queued"
    assert task.result == ""
    assert task.messages[-1] == {"role": "user", "content": "Second prompt"}
    assert len(llm.calls) == 1
    agent.run(task_id)
    assert len(llm.calls) == 2
    assert [m['content'] for m in db.get_task(task_id).messages if m['role'] == 'user'] == [
        'First prompt', 'Second prompt']


def test_duplicate_approval_never_restarts_finished_response(db):
    llm = FakeLLM([FakeMessage(content="Done. Let me know if you need anything else.")])
    agent = Agent(db, llm)
    task_id = db.add_task("hello")
    agent.run(task_id)
    for _ in range(5):
        assert agent.resume_after_confirm(task_id, True).status is Status.DONE
    assert len(llm.calls) == 1


def test_old_automatic_nudges_are_hidden(db):
    task_id = db.add_task('Hello')
    db.add_message(task_id, 'user', {'role': 'user', 'content': 'Hello'})
    db.add_message(task_id, 'user', {'role': 'user', 'content':
        'You described work you have not done. Do not describe. Call the tool now. '
        'Write text only when every part of the task is complete.'})
    assert db.get_task(task_id).messages == [{'role': 'user', 'content': 'Hello'}]
