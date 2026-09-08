import pytest
from peppermint import config
from peppermint.daemon.context import prepare_messages
from peppermint.daemon.llm import LLMError


def test_short_history_is_unchanged():
    messages = [{'role': 'system', 'content': 'Rules'}, {'role': 'user', 'content': 'Hello'}]
    assert prepare_messages(messages, []) == messages


def test_compaction_keeps_whole_recent_turns_without_changing_storage(monkeypatch):
    monkeypatch.setattr(config, 'NUM_CTX', 1800)
    monkeypatch.setattr(config, 'MAX_RESPONSE_TOKENS', 1000)
    old = [{'role': 'user', 'content': 'Old prompt'}, {'role': 'assistant', 'content': 'x'*1500}]
    current = [{'role': 'user', 'content': 'Latest correction'},
               {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'list_dir', 'arguments': {'path': '/tmp'}}}]},
               {'role': 'tool', 'name': 'list_dir', 'content': 'a.txt'}]
    messages = [{'role': 'system', 'content': 'Rules'}]+old+current
    prepared = prepare_messages(messages, [])
    assert prepared[1:] == current
    assert 'omitted' in prepared[0]['content']
    assert messages[0]['content'] == 'Rules'
    assert len(messages) == 6


def test_oversized_active_turn_fails_explicitly(monkeypatch):
    monkeypatch.setattr(config, 'NUM_CTX', 1200)
    monkeypatch.setattr(config, 'MAX_RESPONSE_TOKENS', 1000)
    with pytest.raises(LLMError, match='too large'):
        prepare_messages([{'role': 'user', 'content': 'x'*3000}], [])
