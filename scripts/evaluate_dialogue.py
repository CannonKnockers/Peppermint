#!/usr/bin/env python3
"""Evaluate first-turn dialogue routing with a real model and isolated Agent.

Computer tools are blocked before their functions run. Native cases exercise the
first model response; forced cases supply a recorded fixture draft first, then
use the real model for the production schema-constrained dialogue decision.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import ollama

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon import tools
from peppermint.daemon.agent import Agent, system_prompt
from peppermint.daemon.db import Database
from peppermint.daemon.llm import LLM, LLMError
from peppermint.daemon.tools.registry import ToolError


# Declared before running the dialogue protocol against any candidate. Forced
# drafts are synthetic examples of the earlier plain-text routing problem.
CASES = [
    dict(id='vague_game', prompt='hey this games not working, fix it',
         expected_status='awaiting-input', clarification_first=True,
         draft='Which game is failing, how do you launch it, and what happens when you try?'),
    dict(id='vague_windows_app', prompt="My Windows application won't open on Linux. Fix it.",
         expected_status='awaiting-input', clarification_first=True,
         draft='Which Windows application is it, how are you running it on Linux, and what error do you see?'),
    dict(id='remote_target', prompt='Fix an application on my other computer. You do not know its OS, application, or connection details.',
         expected_status='awaiting-input', clarification_first=True,
         draft='What operating system and application are on the other computer, what is failing, and how can you access it?'),
    dict(id='cleanup_scope', prompt="Clean up my computer. Get rid of things I don't need.",
         expected_status='awaiting-input', clarification_first=True,
         draft='What should I remove, and which files or applications should I preserve?'),
    dict(id='missing_destination', prompt='Move /fixture/invoice.pdf to the folder I normally use for bills.',
         expected_status='awaiting-input', clarification_first=True,
         draft='What is the path of the folder you normally use for bills?'),
    dict(id='greeting', prompt='Hello. Answer briefly without inspecting my computer.',
         expected_status='done', draft='Hello!'),
    dict(id='impossible_hardware', prompt='What bash command can turn my 8 GB of physical VRAM into 16 GB without changing hardware?',
         expected_status='done',
         draft='No bash command can increase physical VRAM. Increasing its physical capacity requires different hardware.'),
    dict(id='quoted_question', prompt="Reply exactly with the quotation 'Which game is failing?' Do not ask me anything.",
         expected_status='done', answer_contains=['Which game is failing?'],
         draft='Which game is failing?'),
    dict(id='explanation_only', prompt='Explain what information you would need to troubleshoot a game. Do not ask me to supply it or inspect anything.',
         expected_status='done',
         draft="I would need the game's name, launcher, operating system, and observed failure symptom."),
    dict(id='retest_question', prompt='Ask me whether Steam game Example, AppID 480, now launches successfully. Do not run anything.',
         expected_status='awaiting-input',
         draft='Can you launch Example again and tell me whether it reaches the main menu successfully?'),
    dict(id='two_choices', prompt='Ask whether I want game troubleshooting or Windows file-sharing help. Give exactly those two choices and wait.',
         expected_status='awaiting-input', options=2,
         draft='Which would you like help with?\n1. Game troubleshooting\n2. Windows file-sharing help'),
]

# Native-only holdout declared before the general scope-priority prompt change
# was evaluated. These are frozen examples, not replacements for existing cases.
HOLDOUT_CASES = [
    dict(id='archive_destination',
         prompt='Copy /fixture/incident-report.txt into the directory I normally use for archived reports.',
         expected_status='awaiting-input', clarification_first=True),
    dict(id='writer_ask_only',
         prompt='Ask me whether LibreOffice Writer still freezes when I open a document. Do not inspect or change anything.',
         expected_status='awaiting-input'),
    dict(id='absolute_path_explanation',
         prompt='Explain what an absolute Linux file path is. Use /tmp/report.txt as an example; do not inspect it or ask me for another path.',
         expected_status='done', answer_contains=['/tmp/report.txt']),
    dict(id='conversation_finished',
         prompt='Thanks, that is all. Reply briefly.', expected_status='done'),
]

ALLOWED_TOOLS = frozenset({'ask_user', 'linux_reference', 'set_plan', 'request_retest'})


def without_thinking(value):
    """Copy export data while omitting protocol-only reasoning fields."""
    if isinstance(value, dict):
        return {key: without_thinking(item) for key, item in value.items() if key != 'thinking'}
    if isinstance(value, list):
        return [without_thinking(item) for item in value]
    return value


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def source_snapshot():
    root = Path(__file__).resolve().parents[1]
    snapshots = {}
    for relative in ('scripts/evaluate_dialogue.py', 'peppermint/daemon/agent.py',
                     'peppermint/daemon/dialogue.py', 'peppermint/daemon/llm.py',
                     'peppermint/daemon/context.py', 'peppermint/config.py'):
        contents = (root / relative).read_bytes()
        snapshots[relative] = {'sha256': hashlib.sha256(contents).hexdigest(),
                               'text': contents.decode('utf-8')}
    return snapshots


def guarded_tool_call(original, attempts, blocked):
    def call(name, arguments, ctx):
        proposal = {'tool': name, 'arguments': deepcopy(arguments)}
        attempts.append(proposal)
        if name not in ALLOWED_TOOLS:
            blocked.append(proposal)
            raise ToolError('Dialogue evaluation blocked this computer tool before execution. '
                            'No computer inspection or change occurred.')
        return original(name, arguments, ctx)
    return call


class EvaluationLLM(LLM):
    def __init__(self, model, forced_draft=None, max_model_calls=4):
        super().__init__(model=model)
        self.forced_draft = forced_draft
        self.max_model_calls = max_model_calls
        self.model_calls = 0
        self.events = []

    def chat(self, messages, tools=None, **kwargs):
        event = {
            'request': {'messages': without_thinking(deepcopy(messages)),
                        'messages_sha256': canonical_hash(messages),
                        'omitted_thinking': [{'message_index': i, 'chars': len(m['thinking']),
                                               'sha256': hashlib.sha256(m['thinking'].encode()).hexdigest()}
                                              for i, m in enumerate(messages) if m.get('thinking')],
                        'tools': deepcopy(tools),
                        'response_format': deepcopy(kwargs.get('response_format')),
                        'think': False if kwargs.get('response_format') is not None else config.THINK,
                        'options': deepcopy(config.LLM_OPTIONS)},
            'source': 'model',
        }
        self.last_response_metadata = {}
        started = time.monotonic()
        try:
            if self.forced_draft is not None and not self.events:
                event['source'] = 'forced_fixture_draft'
                reply = ollama.Message(role='assistant', content=self.forced_draft)
            else:
                if self.model_calls >= self.max_model_calls:
                    event['source'] = 'budget_refusal'
                    raise LLMError('Dialogue evaluation reached its actual model-call budget.')
                self.model_calls += 1
                reply = super().chat(messages, tools, **kwargs)
            event['response'] = without_thinking(Agent._to_dict(reply))
            return reply
        except Exception as exc:
            event['error'] = str(exc)
            raise
        finally:
            event['seconds'] = round(time.monotonic() - started, 3)
            event['response_metadata'] = dict(self.last_response_metadata)
            self.events.append(event)


def grade(case, mode, task, attempts, blocked, events):
    failures = []
    if task['status'] != case['expected_status']:
        failures.append(f"Expected {case['expected_status']}; got {task['status']}.")
    if blocked:
        failures.append('Computer tools were proposed and blocked before execution.')
    if case.get('clarification_first') and attempts and attempts[0]['tool'] != 'ask_user':
        failures.append('The first proposed step was not clarification.')
    if task['status'] == Status.AWAITING_INPUT.value:
        if not task.get('question', '').strip():
            failures.append('Awaiting-input has no nonempty question.')
        if not any(p['tool'] in ('ask_user', 'request_retest') for p in attempts):
            failures.append('Question did not pass through a dialogue tool.')
    if case.get('options'):
        asks = [p for p in attempts if p['tool'] == 'ask_user']
        options = asks[-1]['arguments'].get('options') if asks else None
        if (not isinstance(options, list) or len(options) != case['options']
                or any(not isinstance(o, str) or not o.strip() for o in options)):
            failures.append('The expected structured choices were not preserved.')
    if case['expected_status'] == Status.DONE.value:
        answer = task.get('result', '')
        if not answer.strip():
            failures.append('No final answer was retained.')
        if any(term not in answer for term in case.get('answer_contains', [])):
            failures.append('The requested quotation was not preserved.')
        if mode == 'forced' and answer != case['draft']:
            failures.append('The dialogue decision changed the original answer.')
    if mode == 'forced' and not any(e['source'] == 'model' and e['request']['response_format']
                                   for e in events):
        failures.append('The forced draft did not reach the real constrained decision call.')
    if any(e['response_metadata'].get('done_reason') == 'length'
           or e['response_metadata'].get('done') is False for e in events):
        failures.append('A model generation was incomplete.')
    return failures


def evaluate(model_name, mode='all', case_ids=None, max_model_calls=4, output=None, suite='regression'):
    from peppermint.daemon.dialogue import DIALOGUE_INSTRUCTIONS, DIALOGUE_SCHEMA

    sources = source_snapshot()
    if suite not in ('regression', 'holdout'):
        raise ValueError('Unknown dialogue suite.')
    cases = HOLDOUT_CASES if suite == 'holdout' else CASES
    selected = [case for case in cases if case_ids is None or case['id'] in case_ids]
    if not selected:
        raise ValueError('No dialogue cases selected.')
    unknown = set(case_ids or ()) - {case['id'] for case in cases}
    if unknown:
        raise ValueError(f'Unknown dialogue cases: {sorted(unknown)}')
    if suite == 'holdout' and mode == 'forced':
        raise ValueError('The dialogue holdout contains native cases only.')
    modes = ('native',) if suite == 'holdout' and mode == 'all' else (
        ('native', 'forced') if mode == 'all' else (mode,))
    if any(item not in ('native', 'forced') for item in modes) or max_model_calls < 1:
        raise ValueError('Invalid dialogue mode or model-call budget.')
    report = {
        'model': model_name, 'suite': suite, 'modes': list(modes),
        'options': deepcopy(config.LLM_OPTIONS), 'think': config.THINK,
        'system_prompt': system_prompt(), 'dialogue_instructions': DIALOGUE_INSTRUCTIONS,
        'dialogue_schema': deepcopy(DIALOGUE_SCHEMA), 'tool_schemas': tools.schemas(),
        'source_snapshot': sources,
        'allowed_tools': sorted(ALLOWED_TOOLS), 'max_model_calls_per_case': max_model_calls,
        'cases': deepcopy(selected), 'results': [], 'passed': 0, 'total': 0,
        'scope': 'Real model and production Agent with an isolated database; no computer tool executes. '
                 'Forced cases replace only the first response with a synthetic plain-text draft.',
        'limitations': [
            'These narrow routing checks do not establish diagnostic correctness, repair success, or general model quality.',
            'Native cases measure the full first-turn protocol; forced cases isolate fallback routing and are not naturally occurring model replies.',
            'Exported requests omit raw thinking; hashes and lengths identify the exact omitted inputs. Other input messages and schemas are retained.',
            'Questions and answers still need manual review for useful wording, unsupported claims, and preservation of user intent.',
        ],
    }
    for run_mode in modes:
        for case in selected:
            db = Database(':memory:')
            llm = EvaluationLLM(model_name, case['draft'] if run_mode == 'forced' else None,
                                max_model_calls=max_model_calls)
            llm.ensure_model()
            if llm.model != model_name:
                raise ValueError('Requested model is unavailable; refusing to evaluate a fallback.')
            task_id = db.add_task(case['prompt'])
            attempts, blocked = [], []
            started = time.monotonic()
            with patch.object(tools, 'call', guarded_tool_call(tools.call, attempts, blocked)):
                Agent(db, llm).run(task_id)
            task = without_thinking(db.get_task(task_id).to_dict())
            failures = grade(case, run_mode, task, attempts, blocked, llm.events)
            row = {'case': case['id'], 'mode': run_mode, 'failures': failures,
                   'seconds': round(time.monotonic() - started, 3), 'model_calls': llm.model_calls,
                   'agent_chat_invocations': len(llm.events), 'attempts': attempts, 'blocked': blocked,
                   'events': llm.events, 'task': task}
            report['results'].append(row)
            report['total'] = len(report['results'])
            report['passed'] = sum(not r['failures'] for r in report['results'])
            print(f"{'FAIL' if failures else 'PASS'} {run_mode}/{case['id']} "
                  f"{task['status']}; {llm.model_calls} model calls; {failures}", flush=True)
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=config.MODEL)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['native', 'forced', 'all'], default='all')
    parser.add_argument('--suite', choices=['regression', 'holdout'], default='regression')
    parser.add_argument('--case', dest='case_ids', action='append', choices=[case['id'] for case in CASES + HOLDOUT_CASES])
    parser.add_argument('--max-model-calls', type=int, default=4)
    args = parser.parse_args()
    report = evaluate(args.model, args.mode, args.case_ids, args.max_model_calls, args.output, args.suite)
    print(f"{report['passed']}/{report['total']} dialogue checks passed. No computer tool executed.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
