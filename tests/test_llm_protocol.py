"""Exercise Ollama serialization over mocked HTTP, never a model server."""

from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import ollama
import pytest

from peppermint import config
from peppermint.daemon.context import prepare_messages
from peppermint.daemon.llm import LLM, LLMError


def mocked_model(monkeypatch, handler):
    model = LLM(model='fixture-model')
    model._checked = True
    model.client = ollama.Client(host='http://fixture.invalid', transport=httpx.MockTransport(handler))
    async_client = ollama.AsyncClient
    monkeypatch.setattr('peppermint.daemon.llm.ollama.AsyncClient',
                        lambda **kwargs: async_client(**kwargs, transport=httpx.MockTransport(handler)))
    return model


def chat(model, messages, cancellable):
    return model.chat(messages, cancelled=(lambda: False) if cancellable else None)


@pytest.mark.parametrize('cancellable', [False, True])
def test_native_tool_names_survive_actual_client_serialization(monkeypatch, cancellable):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'role': 'assistant', 'content': 'Received.'}})

    messages = [
        {'role': 'assistant', 'content': '', 'thinking': 'fixture reasoning', 'tool_calls': [
            {'function': {'name': 'read_file', 'arguments': {'path': '/fixture/status'}}}]},
        {'role': 'tool', 'name': 'read_file', 'content': 'legacy result'},
        {'role': 'tool', 'name': 'legacy-alias', 'tool_name': 'linux_reference', 'content': 'native result'},
    ]
    saved = deepcopy(messages)
    model = mocked_model(monkeypatch, handler)
    assert chat(model, messages, cancellable).content == 'Received.'
    assert messages == saved
    sent = requests[0]['messages']
    assert sent[0]['thinking'] == 'fixture reasoning'
    assert sent[0]['tool_calls'] == messages[0]['tool_calls']
    assert sent[1] == {'role': 'tool', 'tool_name': 'read_file', 'content': 'legacy result'}
    assert sent[2] == {'role': 'tool', 'tool_name': 'linux_reference', 'content': 'native result'}


@pytest.mark.parametrize('cancellable', [False, True])
def test_structured_dialogue_serializes_schema_without_tools_or_thinking(monkeypatch, cancellable):
    from peppermint.daemon.dialogue import DIALOGUE_SCHEMA
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{"kind":"answer","question":"","options":[]}'}})

    monkeypatch.setattr(config, 'THINK', True)
    model = mocked_model(monkeypatch, handler)
    model.chat([{'role': 'user', 'content': 'Fixture'}], response_format=DIALOGUE_SCHEMA,
               cancelled=(lambda: False) if cancellable else None)
    assert requests[0]['format'] == DIALOGUE_SCHEMA
    assert requests[0]['think'] is False
    assert not requests[0].get('tools')


def test_structured_dialogue_rejects_executable_tool_schemas():
    from peppermint.daemon.dialogue import DIALOGUE_SCHEMA
    with pytest.raises(LLMError, match='cannot include executable tools'):
        LLM().chat([], tools=[{'type': 'function'}], response_format=DIALOGUE_SCHEMA)


@pytest.mark.parametrize('cancellable', [False, True])
def test_response_metrics_capture_generation_limit_without_text(monkeypatch, cancellable):
    reply = {
        'model': 'fixture-model', 'done': True, 'done_reason': 'length',
        'eval_count': 4096, 'prompt_eval_count': 2048, 'eval_duration': 2000000,
        'total_duration': 3000000, 'load_duration': 500000, 'prompt_eval_duration': 500000,
        'message': {'role': 'assistant', 'thinking': 'private fixture reasoning',
                    'content': 'private fixture answer', 'tool_calls': [
                        {'function': {'name': 'fixture', 'arguments': {'secret': 'private fixture argument'}}}]},
    }
    model = mocked_model(monkeypatch, lambda request: httpx.Response(200, json=reply))
    message = chat(model, [{'role': 'user', 'content': 'private fixture request'}], cancellable)
    assert message.content == reply['message']['content']
    assert model.last_response_metadata == {
        'done': True, 'done_reason': 'length', 'eval_count': 4096, 'prompt_eval_count': 2048,
        'eval_duration': 2000000, 'total_duration': 3000000, 'load_duration': 500000,
        'prompt_eval_duration': 500000, 'thinking_chars': len(reply['message']['thinking']),
        'content_chars': len(reply['message']['content']), 'tool_call_count': 1,
    }
    assert 'private fixture' not in json.dumps(model.last_response_metadata)


@pytest.mark.parametrize('cancellable', [False, True])
def test_failed_request_cannot_reuse_previous_metrics(monkeypatch, cancellable):
    responses = iter([
        httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                                  'message': {'role': 'assistant', 'content': 'A reply.'}}),
        httpx.Response(500, json={'error': 'fixture failure'}),
    ])
    model = mocked_model(monkeypatch, lambda request: next(responses))
    chat(model, [], cancellable)
    assert model.last_response_metadata['done_reason'] == 'stop'
    with pytest.raises(LLMError, match='fixture failure'):
        chat(model, [], cancellable)
    assert model.last_response_metadata == {}


def test_model_lookup_failure_clears_previous_metrics(monkeypatch):
    model = LLM()
    model.last_response_metadata = {'done_reason': 'stop'}

    def unavailable():
        raise LLMError('fixture model unavailable')

    monkeypatch.setattr(model, 'ensure_model', unavailable)
    with pytest.raises(LLMError, match='fixture model unavailable'):
        model.chat([])
    assert model.last_response_metadata == {}


def test_metrics_allow_older_fake_replies_without_envelope_fields():
    model = LLM()
    model._checked = True
    message = SimpleNamespace(role='assistant', content='A reply.')
    model.client = SimpleNamespace(chat=lambda **kwargs: SimpleNamespace(message=message))
    assert model.chat([]) is message
    assert model.last_response_metadata == {'content_chars': len(message.content)}


def test_invalid_optional_metrics_are_omitted():
    model = LLM()
    model._record_response_metadata(SimpleNamespace(
        done='unexpected', done_reason=None, eval_count=True, eval_duration=-1,
        total_duration='private fixture data', prompt_eval_cached_count=42,
        message=SimpleNamespace(content=None, thinking=None, tool_calls=None)))
    assert model.last_response_metadata == {'prompt_eval_cached_count': 42}


def test_only_explicit_user_recovery_is_visible_and_markers_are_removed():
    messages = [
        {'role': 'system', 'content': 'Rules.'},
        {'role': 'user', 'content': 'Original request.'},
        {'role': 'assistant', 'content': ''},
        {'role': 'assistant', 'internal': True, 'content': 'hidden bookkeeping'},
        {'role': 'system', 'internal': True, 'model_visible': True, 'content': 'hidden system bookkeeping'},
        {'role': 'user', 'internal': True, 'model_visible': 'yes', 'content': 'not explicitly enabled'},
        {'role': 'user', 'internal': True, 'model_visible': True, 'content': 'Continue.'},
    ]
    saved = deepcopy(messages)
    assert prepare_messages(messages, []) == [
        {'role': 'system', 'content': 'Rules.'},
        {'role': 'user', 'content': 'Original request.'},
        {'role': 'assistant', 'content': ''},
        {'role': 'user', 'content': 'Continue.'},
    ]
    assert messages == saved


def test_recovery_cannot_replace_oversized_active_request(monkeypatch):
    monkeypatch.setattr(config, 'NUM_CTX', 1800)
    monkeypatch.setattr(config, 'MAX_RESPONSE_TOKENS', 1000)
    messages = [
        {'role': 'system', 'content': 'Rules.'},
        {'role': 'user', 'content': 'Original request: ' + 'x' * 1800},
        {'role': 'assistant', 'content': ''},
        {'role': 'user', 'internal': True, 'model_visible': True, 'content': 'Continue.'},
    ]
    with pytest.raises(LLMError, match='too large'):
        prepare_messages(messages, [])
