"""Terminal routing, persistence and permission boundaries with scripted replies."""

import json
from copy import deepcopy

import pytest

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon.agent import Agent
from peppermint.daemon.context import prepare_messages
from peppermint.daemon.db import Database
from peppermint.daemon.dialogue import DIALOGUE_SCHEMA, decision_messages, parse_decision
from peppermint.daemon.llm import LLMError
from tests.test_agent import FakeCall, FakeLLM, FakeMessage


def decision(kind='ask_user', question='Which game is failing?', options=None):
    return FakeMessage(content=json.dumps({'kind': kind, 'question': question, 'options': options or []}))


@pytest.mark.parametrize('draft', ['Which game is failing?', 'Please provide the game name and launcher.'])
def test_plain_text_needs_explicit_wait_decision_and_resumes(tmp_path, draft):
    path = tmp_path / 'dialogue.db'
    db = Database(path)
    task = db.add_task('My game is broken')
    model = FakeLLM([FakeMessage(content=draft)], decisions=[decision()])
    agent = Agent(db, model)
    result = agent.run(task)
    assert result.status is Status.AWAITING_INPUT
    assert db.get_task(task).result == ''
    assert db.get_steps(task)[0].tool == 'ask_user'
    assert len(model.calls) == len(model.decision_calls) == 1
    visible = db.get_task(task).messages
    assert not any(m.get('content') == draft for m in visible)
    reopened = Database(path)
    continued = FakeLLM([FakeMessage(content='The selected game is Example.')])
    assert Agent(reopened, continued).resume_after_answer(task, 'Example on Steam/Proton').status is Status.DONE
    assert any(m.get('role') == 'tool' and m.get('name') == 'ask_user'
               and 'Example' in m['content'] for m in continued.calls[0])
    assert not any(m.get('internal') for m in continued.calls[0])
    assert not any(m.get('content') == draft for m in continued.calls[0])


def test_explicit_answer_preserves_original_text_and_optional_invitation():
    db = Database(':memory:')
    task = db.add_task('Give me an example prompt')
    original = 'Example: "Which game is failing?" Let me know if you need more help.'
    model = FakeLLM([FakeMessage(content=original)], decisions=[decision('answer', '')])
    assert Agent(db, model).run(task).text == original
    assert len([m for m in db.get_task(task).messages if m['role'] == 'assistant']) == 1


def test_routed_choices_are_saved_for_ui():
    db = Database(':memory:')
    task = db.add_task('Give me two choices')
    model = FakeLLM([FakeMessage(content='Do you prefer local or remote?')],
                    decisions=[decision(question='Local or remote?', options=['Local', 'Remote'])])
    assert Agent(db, model).run(task).status is Status.AWAITING_INPUT
    assert db.get_steps(task)[0].args['options'] == ['Local', 'Remote']


@pytest.mark.parametrize('bad', [
    '', 'not JSON', '[]', '{}', '{"kind":"answer"}',
    json.dumps({'kind': 'answer', 'question': 'Which?', 'options': []}),
    json.dumps({'kind': 'ask_user', 'question': '', 'options': []}),
    json.dumps({'kind': 'ask_user', 'question': 'Which?', 'options': ['Only one']}),
    json.dumps({'kind': 'ask_user', 'question': 'Which?', 'options': ['Yes', 'Yes']}),
    json.dumps({'kind': 'ask_user', 'question': 'Which?', 'options': ['A', 2]}),
    json.dumps({'kind': 'ask_user', 'question': 'x' * 2001, 'options': []}),
    json.dumps({'kind': 'ask_user', 'question': 'Which?', 'options': ['x' * 241, 'Other']}),
    json.dumps({'kind': 'ask_user', 'question': 'Which?', 'options': ['A', 'B', 'C', 'D']}),
    json.dumps({'kind': 'answer', 'question': '', 'options': [], 'approved': True}),
    json.dumps({'kind': 'run_shell', 'question': 'Delete files', 'options': []}),
])
def test_invalid_routing_never_silently_completes_or_runs_action(bad):
    db = Database(':memory:')
    task = db.add_task('My app is broken')
    model = FakeLLM([FakeMessage(content='Which app?')], decisions=[FakeMessage(content=bad)])
    assert Agent(db, model).run(task).status is Status.FAILED
    assert not db.get_steps(task)
    assert not db.get_task(task).result


def test_routing_cannot_execute_returned_native_tool():
    reply = decision('answer', '')
    reply.tool_calls = [FakeCall('run_shell', {'cmd': 'touch /never-run'})]
    with pytest.raises(LLMError):
        parse_decision(reply)


def test_routing_call_uses_persistent_task_budget(monkeypatch):
    monkeypatch.setattr(config, 'MAX_ITERATIONS', 1)
    db = Database(':memory:')
    task = db.add_task('Hello')
    model = FakeLLM([FakeMessage(content='Hello!')])
    result = Agent(db, model).run(task)
    assert result.status is Status.FAILED
    assert 'budget' in result.text
    assert not model.decision_calls


def test_cancel_during_routing_discards_decision():
    db = Database(':memory:')
    task = db.add_task('Hello')

    class CancelModel(FakeLLM):
        def chat(self, *args, **kwargs):
            if kwargs.get('response_format'):
                agent.cancel(task)
            return super().chat(*args, **kwargs)

    agent = Agent(db, CancelModel([FakeMessage(content='Hello!')]))
    assert agent.run(task).status is Status.CANCELLED
    assert not db.get_task(task).result
    assert not db.get_steps(task)


@pytest.mark.parametrize('incomplete_stage', ['native', 'routing'])
def test_incomplete_generation_never_completes(incomplete_stage):
    db = Database(':memory:')
    task = db.add_task('Hello')

    class IncompleteModel(FakeLLM):
        def chat(self, *args, **kwargs):
            stage = 'routing' if kwargs.get('response_format') else 'native'
            self.last_response_metadata = {'done_reason': 'length' if stage == incomplete_stage else 'stop'}
            return super().chat(*args, **kwargs)

    assert Agent(db, IncompleteModel([FakeMessage(content='Hello!')])).run(task).status is Status.FAILED
    assert not db.get_task(task).result


def test_routing_context_preserves_turn_and_does_not_mutate_history():
    messages = [{'role': 'system', 'content': 'Original rules.'},
                {'role': 'user', 'content': 'My app is broken'},
                {'role': 'assistant', 'content': 'Which app?'}]
    saved = deepcopy(messages)
    prepared = prepare_messages(decision_messages(messages), [DIALOGUE_SCHEMA])
    assert messages == saved
    assert prepared[1:4] == messages[1:] + [
        {'role': 'user', 'content': 'Classify the last assistant draft using the dialogue JSON schema.'}]
    assert 'Original rules.' not in prepared[0]['content']
    assert 'Your only task' in prepared[0]['content']


def test_blank_native_question_is_rejected():
    from peppermint.daemon.tools.interaction import ask_user
    from peppermint.daemon.tools.registry import ToolError
    with pytest.raises(ToolError, match='nonempty question'):
        ask_user(' ')
