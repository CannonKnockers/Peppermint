"""Bound model context while leaving the user's stored transcript intact."""
from __future__ import annotations

import json

from peppermint import config
from peppermint.daemon.llm import LLMError


def prepare_messages(messages: list[dict], schemas: list[dict]) -> list[dict]:
    """Retain the system instructions and the newest complete user turns.

    Character accounting is conservative for typical English/tool JSON, not an
    exact tokenizer. Never cut a tool call away from its result. If even the
    active turn exceeds the budget, stop explicitly instead of silently losing
    its instructions. The full history remains in SQLite.
    """
    system = []
    turns: list[list[dict]] = []
    for message in messages:
        # A recovery nudge belongs to the active turn, so compaction cannot
        # retain "Continue" while dropping the request it refers to. Other
        # internal bookkeeping remains invisible to the model.
        recovery = (message.get('internal') and message.get('model_visible') is True
                    and message.get('role') == 'user')
        if message.get('internal') and not recovery:
            continue
        message = {k: v for k, v in message.items() if k not in ('internal', 'model_visible')}
        if message.get('role') == 'system':
            system.append(message)
            continue
        if (message.get('role') == 'user' and not recovery) or not turns:
            turns.append([])
        turns[-1].append(message)
    budget = max(1024, (config.NUM_CTX-config.MAX_RESPONSE_TOKENS)*2)
    size = lambda value: len(json.dumps(value, ensure_ascii=False))
    used = size(system)+size(schemas)
    kept: list[list[dict]] = []
    for turn in reversed(turns):
        if used+size(turn) > budget:
            if not kept:
                raise LLMError('This conversation turn is too large for the local model. '
                               'Start a new task with a shorter request. Your history is saved.')
            break
        kept.insert(0, turn)
        used += size(turn)
    if len(kept) < len(turns):
        system = [dict(m) for m in system]
        if system:
            system[0]['content'] += ('\nOlder conversation turns were omitted to fit memory. '
                                     'Do not guess missing details; ask if they are needed.')
    return system+[m for turn in kept for m in turn]
