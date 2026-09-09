#!/usr/bin/env python3
"""Real-model retest selection with synthetic inspection and typed user reports."""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from peppermint import config
from peppermint.daemon import tools
from peppermint.daemon.agent import Agent, system_prompt
from peppermint.daemon.db import Database
from peppermint.daemon.dialogue import DIALOGUE_INSTRUCTIONS, DIALOGUE_SCHEMA
from peppermint.daemon.tools.registry import Context
from peppermint.common.retest import read_record
from scripts.evaluate_dialogue import (ALLOWED_TOOLS, canonical_hash, guarded_tool_call,
                                      source_snapshot, without_thinking)
from scripts.evaluate_support_workflow import BoundedLLM


TARGET = 'Retest Steam/Proton Example, AppID 480, reaching its main menu'
PROMPT = ('The recorded checks for Steam/Proton Example, AppID 480, are finished. '
          'Its original symptom was closing before the main menu. Ask me to retest '
          'whether it now reaches the main menu for the pending verification step. '
          'Use the recorded checks; do not perform any new inspection or computer action.')


class TracedLLM(BoundedLLM):
    def __init__(self, model):
        super().__init__(model)
        self.events = []

    def chat(self, messages, tools=None, **kwargs):
        event = {'request': {'messages': without_thinking(deepcopy(messages)),
                             'messages_sha256': canonical_hash(messages),
                             'tools': deepcopy(tools),
                             'response_format': deepcopy(kwargs.get('response_format')),
                             'think': False if kwargs.get('response_format') is not None else config.THINK},
                 'model_request_sent': self.calls < 8}
        self.last_response_metadata = {}
        started = time.monotonic()
        try:
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


def seed_task(db):
    task_id = db.add_task(PROMPT)
    output = json.dumps({'fixture': True, 'app_id': '480', 'verified_fix': False,
                         'observations': 'Synthetic installation checks finished; game launch was not tested.'})
    inspection = db.add_step(task_id, 'steam_game_diagnostics', {'app_id': '480'}, 'safe', output, 'ok')
    tools.call('set_plan', {'steps': [
        {'description': 'Inspect Steam/Proton Example, AppID 480', 'kind': 'inspection',
         'status': 'done', 'evidence_step_id': inspection},
        {'description': TARGET, 'kind': 'verification', 'status': 'pending'},
    ]}, Context(task_id, db, require_approval=True))
    messages = [
        {'role': 'system', 'content': system_prompt()},
        {'role': 'user', 'content': 'Inspect Steam/Proton Example, AppID 480; it closes before the main menu.'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'steam_game_diagnostics', 'arguments': {'app_id': '480'}}}]},
        {'role': 'tool', 'name': 'steam_game_diagnostics',
         'content': f'Recorded successful inspection step {inspection}. ' + output},
        {'role': 'user', 'content': PROMPT},
    ]
    for message in messages:
        db.add_message(task_id, message['role'], message)
    return task_id


def evaluate(model_name, output=None):
    sources = source_snapshot()
    root = Path(__file__).resolve().parents[1]
    for relative in ('scripts/evaluate_retest_workflow.py', 'scripts/evaluate_support_workflow.py',
                     'peppermint/daemon/tools/retest.py', 'peppermint/daemon/tools/planning.py',
                     'peppermint/daemon/db.py', 'peppermint/common/retest.py'):
        data = (root / relative).read_bytes()
        sources[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'text': data.decode()}
    report = {'model': model_name, 'options': deepcopy(config.LLM_OPTIONS), 'think': config.THINK,
              'prompt': PROMPT, 'verification_target': TARGET, 'typed_outcomes': ['passed', 'failed'],
              'tool_schemas': tools.schemas(), 'dialogue_schema': deepcopy(DIALOGUE_SCHEMA),
              'dialogue_instructions': DIALOGUE_INSTRUCTIONS, 'source_snapshot': sources,
              'allowed_tools': sorted(ALLOWED_TOOLS), 'results': [], 'passed': 0, 'total': 0,
              'scope': 'The real model selects the retest tool. Inspection evidence and typed user '
                       'attestations are fixtures in separate in-memory tasks; no game is inspected, '
                       'launched or repaired. A passing receipt is user-reported, never independently verified.',
              'limitations': ['This tests protocol selection and bound retest state, not diagnosis or repair quality.',
                              'Raw thinking is omitted from exported messages; input hashes retain its identity.',
                              'Typed outcomes are simulated by the evaluator, not reported by the real user.']}
    for outcome in report['typed_outcomes']:
        db = Database(':memory:')
        task_id = seed_task(db)
        seeded = db.get_task(task_id).to_dict()
        target_id = seeded['plan'][1]['verification_target_id']
        llm = TracedLLM(model_name)
        llm.ensure_model()
        if llm.model != model_name:
            raise ValueError('Requested model unavailable; refusing a fallback.')
        attempts, blocked, failures = [], [], []
        started = time.monotonic()
        with patch.object(tools, 'call', guarded_tool_call(tools.call, attempts, blocked)):
            agent = Agent(db, llm)
            agent.run(task_id)
            requested = db.get_task(task_id).to_dict()
            retest = requested.get('retest')
            if not retest:
                failures.append('The model did not request a typed retest; a generic question is insufficient.')
            elif retest['target_description'] != TARGET or retest['verification_step'] != 2:
                failures.append('The retest selected a different verification target.')
            else:
                agent.resume_after_retest(task_id, retest['request_id'], outcome)
        final = db.get_task(task_id).to_dict()
        if final['status'] not in ('done', 'awaiting-input'):
            failures.append('The workflow did not return an answer or a question within its budget.')
        target = final['plan'][1] if len(final['plan']) > 1 else {}
        if target.get('verification_target_id') != target_id or target.get('description') != TARGET:
            failures.append('The original verification target identity changed.')
        if final['plan'][:1] != seeded['plan'][:1]:
            failures.append('The completed inspection plan row changed.')
        receipt = next((step for step in final['steps'] if retest and step['tool'] == 'request_retest'
                        and (read_record(step['output']) or {}).get('request_id') == retest['request_id']), None)
        if not receipt or receipt['status'] != 'user_reported' or read_record(receipt['output'])['outcome'] != outcome:
            failures.append('The exact typed fixture report was not retained.')
        if outcome == 'passed':
            if (final['status'] != 'done' or target.get('status') != 'done'
                    or target.get('evidence_kind') != 'user_reported'
                    or target.get('evidence_tool') != 'request_retest'
                    or target.get('evidence_step_id') != (receipt or {}).get('id')):
                failures.append('Passed did not complete only the bound target as user-reported.')
            if 'user-reported' not in final.get('result', ''):
                failures.append('The final answer did not identify the report as user-reported.')
        elif (target.get('status') not in ('pending', 'in_progress')
              or target.get('evidence_kind') is not None
              or target.get('evidence_tool') is not None
              or target.get('evidence_step_id') not in (None, 0)):
            failures.append('Failed incorrectly completed verification or attached completion evidence.')
        if outcome == 'failed' and final['status'] != 'awaiting-input':
            failures.append('The failed retest did not remain awaiting input with unfinished verification.')
        if blocked:
            failures.append('Computer tools were proposed and blocked before execution.')
        if any(event['response_metadata'].get('done_reason') == 'length'
               or event['response_metadata'].get('done') is False for event in llm.events):
            failures.append('A model generation was incomplete.')
        row = {'outcome': outcome, 'failures': failures, 'seconds': round(time.monotonic() - started, 3),
               'seeded_task': without_thinking(seeded), 'requested_task': without_thinking(requested),
               'final_task': without_thinking(final), 'events': llm.events,
               'model_calls': sum(e['model_request_sent'] for e in llm.events),
               'attempts': attempts, 'blocked': blocked}
        report['results'].append(row)
        report['total'] = len(report['results'])
        report['passed'] = sum(not row['failures'] for row in report['results'])
        print(f"{'FAIL' if failures else 'PASS'} {outcome}: {requested['status']} -> "
              f"{final['status']}; {row['model_calls']} model calls; {failures}", flush=True)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=config.MODEL)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.model, args.output)
    print(f"{report['passed']}/{report['total']} retest workflow checks passed. User outcomes were fixtures.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
