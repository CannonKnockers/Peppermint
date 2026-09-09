import json

import ollama

from peppermint.daemon.llm import LLM
from scripts.evaluate_retest_workflow import TARGET, evaluate


def test_real_agent_fixture_retest_paths_with_scripted_model(monkeypatch):
    monkeypatch.setattr(LLM, 'ensure_model', lambda self: self.model)

    def scripted_chat(self, messages, tools=None, **kwargs):
        self.last_response_metadata = {'done': True, 'done_reason': 'stop'}
        if kwargs.get('response_format') is not None:
            return ollama.Message(role='assistant', content=json.dumps(
                {'kind': 'ask_user', 'question': 'What happened when the game failed?', 'options': []}))
        if any(m.get('role') == 'user' and m.get('content', '').startswith('Retest report: Still failing')
               for m in messages):
            return ollama.Message(role='assistant', content='What happened when the game failed?')
        return ollama.Message(role='assistant', tool_calls=[{'function': {
            'name': 'request_retest', 'arguments': {'verification_step': 2,
                                                  'symptom': 'Launch Example AppID 480 and check its main menu.'}}}])

    monkeypatch.setattr(LLM, 'chat', scripted_chat)
    report = evaluate('fixture-model')
    assert report['passed'] == report['total'] == 2
    passed, failed = report['results']
    assert passed['model_calls'] == 1
    assert passed['final_task']['plan'][1]['description'] == TARGET
    assert passed['final_task']['plan'][1]['evidence_kind'] == 'user_reported'
    assert failed['final_task']['plan'][1]['status'] == 'pending'
    assert failed['final_task']['status'] == 'awaiting-input'
    assert not passed['blocked'] and not failed['blocked']


def test_generic_question_does_not_pass_retest_selection(monkeypatch):
    monkeypatch.setattr(LLM, 'ensure_model', lambda self: self.model)

    def scripted_chat(self, messages, tools=None, **kwargs):
        self.last_response_metadata = {'done': True, 'done_reason': 'stop'}
        return ollama.Message(role='assistant', tool_calls=[{'function': {
            'name': 'ask_user', 'arguments': {'question': 'Does the game work now?'}}}])

    monkeypatch.setattr(LLM, 'chat', scripted_chat)
    report = evaluate('fixture-model')
    assert report['passed'] == 0
    assert all('The model did not request a typed retest; a generic question is insufficient.'
               in row['failures'] for row in report['results'])
