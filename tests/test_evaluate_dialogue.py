from copy import deepcopy
import hashlib
import json

import ollama
import pytest

from peppermint import config
from peppermint.daemon import tools
from peppermint.daemon.llm import LLM, LLMError
from peppermint.daemon.tools.registry import ToolError
from scripts.evaluate_dialogue import (ALLOWED_TOOLS, CASES, HOLDOUT_CASES, EvaluationLLM, evaluate,
                                      grade, guarded_tool_call, without_thinking)


def test_dialogue_guard_blocks_every_registered_computer_tool_before_function():
    called, attempts, blocked = [], [], []
    guard = guarded_tool_call(lambda *args: called.append(args), attempts, blocked)
    computer_tools = set(tools.tool_names()) - ALLOWED_TOOLS
    assert computer_tools
    for name in computer_tools:
        with pytest.raises(ToolError, match='blocked'):
            guard(name, {'fixture': True}, object())
    assert not called
    assert {p['tool'] for p in blocked} == computer_tools
    assert len(attempts) == len(computer_tools)


def test_dialogue_guard_preserves_registry_validation_for_allowed_tools():
    called, attempts, blocked = [], [], []

    def original(name, args, ctx):
        called.append((name, args, ctx))
        raise ToolError('registry rejected this fixture')

    guard = guarded_tool_call(original, attempts, blocked)
    with pytest.raises(ToolError, match='registry rejected'):
        guard('ask_user', {'options': ['only one']}, None)
    assert len(called) == 1
    assert not blocked


def test_export_removes_reasoning_without_mutating_protocol_history():
    value = {'events': [{'request': {'messages': [{'role': 'assistant', 'thinking': 'private input', 'content': 'draft'}]},
                         'response': {'thinking': 'private output', 'content': 'answer'}}],
             'task': {'messages': [{'role': 'assistant', 'thinking': 'private history', 'content': 'visible'}]}}
    original = deepcopy(value)
    exported = without_thinking(value)
    assert 'private' not in str(exported)
    assert exported['task']['messages'][0]['content'] == 'visible'
    assert value == original


def test_grade_requires_real_fallback_and_preserves_completed_answer():
    case = next(c for c in CASES if c['id'] == 'quoted_question')
    task = {'status': 'done', 'result': case['draft']}
    assert grade(case, 'forced', task, [], [], []) == [
        'The forced draft did not reach the real constrained decision call.']
    events = [{'source': 'model', 'request': {'response_format': {'type': 'object'}},
               'response_metadata': {'done': True, 'done_reason': 'stop'}}]
    assert not grade(case, 'forced', task, [], [], events)
    task['result'] += ' Extra text.'
    assert 'The dialogue decision changed the original answer.' in grade(case, 'forced', task, [], [], events)


def test_grade_does_not_hide_unsafe_proposal_followed_by_correct_question():
    case = next(c for c in CASES if c['id'] == 'vague_game')
    attempts = [{'tool': 'run_shell', 'arguments': {}},
                {'tool': 'ask_user', 'arguments': {'question': 'Which game?'}}]
    task = {'status': 'awaiting-input', 'question': 'Which game?'}
    failures = grade(case, 'native', task, attempts, attempts[:1], [])
    assert 'Computer tools were proposed and blocked before execution.' in failures
    assert 'The first proposed step was not clarification.' in failures


def test_grade_rejects_incomplete_generation_and_unstructured_choices():
    case = next(c for c in CASES if c['id'] == 'two_choices')
    task = {'status': 'awaiting-input', 'question': 'Which one?'}
    attempts = [{'tool': 'ask_user', 'arguments': {'question': 'Which one?'}}]
    events = [{'source': 'model', 'request': {'response_format': None},
               'response_metadata': {'done_reason': 'length'}}]
    failures = grade(case, 'native', task, attempts, [], events)
    assert 'The expected structured choices were not preserved.' in failures
    assert 'A model generation was incomplete.' in failures


def test_evaluator_exercises_real_agent_routing_and_records_actual_call_counts(monkeypatch):
    monkeypatch.setattr(config, 'THINK', True)
    monkeypatch.setattr(LLM, 'ensure_model', lambda self: self.model)

    def scripted_chat(self, messages, tools=None, **kwargs):
        self.last_response_metadata = {'done': True, 'done_reason': 'stop'}
        prompt = next(m['content'] for m in messages if m['role'] == 'user')
        quoted = prompt.startswith('Reply exactly')
        if kwargs.get('response_format') is not None:
            assert not tools
            payload = {'kind': 'answer' if quoted else 'ask_user',
                       'question': '' if quoted else 'Which game is failing?', 'options': []}
            return ollama.Message(role='assistant', content=json.dumps(payload))
        return ollama.Message(role='assistant', content='Which game is failing?')

    monkeypatch.setattr(LLM, 'chat', scripted_chat)
    report = evaluate('fixture-model', case_ids=['vague_game', 'quoted_question'])
    assert report['passed'] == report['total'] == 4
    assert 'peppermint/daemon/dialogue.py' in report['source_snapshot']
    for snapshot in report['source_snapshot'].values():
        assert snapshot['sha256'] == hashlib.sha256(snapshot['text'].encode()).hexdigest()
    for row in report['results']:
        assert not row['blocked']
        assert row['model_calls'] == (1 if row['mode'] == 'forced' else 2)
        assert row['agent_chat_invocations'] == 2
        assert row['events'][0]['request']['think'] is True
        assert row['events'][1]['request']['think'] is False
        assert row['events'][1]['request']['response_format'] == report['dialogue_schema']


def test_evaluator_fixture_does_not_spend_actual_model_budget_and_refusal_clears_metrics(monkeypatch):
    def scripted_chat(self, *args, **kwargs):
        self.last_response_metadata = {'done': True, 'done_reason': 'stop'}
        return ollama.Message(role='assistant', content='Fixture answer.')

    monkeypatch.setattr(LLM, 'chat', scripted_chat)
    llm = EvaluationLLM('fixture-model', forced_draft='Which game?', max_model_calls=1)
    assert llm.chat([]).content == 'Which game?'
    assert llm.model_calls == 0
    assert llm.chat([]).content == 'Fixture answer.'
    assert llm.model_calls == 1
    with pytest.raises(LLMError, match='actual model-call budget'):
        llm.chat([])
    assert llm.model_calls == 1
    assert llm.events[-1]['source'] == 'budget_refusal'
    assert llm.events[-1]['response_metadata'] == {}


def test_native_holdout_is_separate_and_never_uses_forced_drafts(monkeypatch):
    monkeypatch.setattr(LLM, 'ensure_model', lambda self: self.model)
    prompts = {case['prompt']: case for case in HOLDOUT_CASES}

    def scripted_chat(self, messages, tools=None, **kwargs):
        self.last_response_metadata = {'done': True, 'done_reason': 'stop'}
        prompt = next(m['content'] for m in messages if m['role'] == 'user')
        case = prompts[prompt]
        if kwargs.get('response_format') is not None:
            return ollama.Message(role='assistant', content=json.dumps(
                {'kind': 'answer', 'question': '', 'options': []}))
        if case['expected_status'] == 'awaiting-input':
            return ollama.Message(role='assistant', tool_calls=[{'function': {
                'name': 'ask_user', 'arguments': {'question': 'Fixture clarification?'}}}])
        return ollama.Message(role='assistant', content='Fixture explanation with /tmp/report.txt.')

    monkeypatch.setattr(LLM, 'chat', scripted_chat)
    report = evaluate('fixture-model', suite='holdout')
    assert report['passed'] == report['total'] == 4
    assert report['suite'] == 'holdout'
    assert report['modes'] == ['native']
    assert {row['case'] for row in report['results']} == {case['id'] for case in HOLDOUT_CASES}
    assert not ({case['id'] for case in HOLDOUT_CASES} & {case['id'] for case in CASES})
    assert all(event['source'] == 'model' for row in report['results'] for event in row['events'])
    with pytest.raises(ValueError, match='native cases only'):
        evaluate('fixture-model', mode='forced', suite='holdout')
