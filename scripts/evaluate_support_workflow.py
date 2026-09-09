#!/usr/bin/env python3
"""Exercise the real model/agent with simulated Steam evidence, never live actions.

Only bundled references, questions, planning, and a fixture replacement for the
Steam diagnostic can run. Approval of that fixture happens in a transient task
database. Every other computer tool is blocked, even if the model requests it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from peppermint import config
from peppermint.common.models import Status
from peppermint.daemon import tools
from peppermint.daemon.agent import Agent, system_prompt
from peppermint.daemon.db import Database
from peppermint.daemon.llm import LLM, LLMError
from peppermint.daemon.tools.registry import INTERNAL_TOOLS, ToolError


def fixture_diagnostic(app_id: str = '', log_path: str = '') -> str:
    # Saved baseline reports retain graphics.vulkan.status='unavailable'. New
    # runs use the real tool's explicit command availability and runtime scope.
    if app_id not in ('', '480'):
        raise ToolError('The selected fixture game has AppID 480; do not choose a different game.')
    common = dict(schema_version=1, fixture=True, changed_settings=False, verified_fix=False)
    if not app_id:
        return json.dumps(dict(common, mode='inventory', selection='needs_user_game_selection',
                               installed_games=[{'app_id': '480', 'name': 'Example'},
                                                {'app_id': '481', 'name': 'Another Game'}]))
    return json.dumps(dict(common, mode='diagnostics', requested_app_id='480',
        selection='explicit_app_id',
        installed_games=[{'app_id': '480', 'name': 'Example', 'client': 'native', 'library': '/fixture/Steam'}],
        installations=[{'game_directory': {'exists': True}, 'compatibility_prefix': {'exists': True},
                        'storage': {'filesystem': 'ext4', 'free_bytes': 53687091200}}],
        logs=[{'path': '/fixture/steam-480.log', 'status': 'read', 'trust': 'untrusted_log_data',
               'failure_run_match': 'unknown', 'tail': 'Failed to create Vulkan instance: VK_ERROR_INCOMPATIBLE_DRIVER'}],
        graphics={'vulkan_probe': {'command': 'vulkaninfo', 'command_available': False,
                                   'status': 'command_missing',
                                   'reason': 'vulkaninfo was not found in standard system command paths'},
                  'scope': 'host_only', 'game_runtime_health': 'unverified',
                  'game_vulkan_support': 'unverified',
                  'limitations': 'Probe status describes the diagnostic command, not graphics health. '
                                 'A missing command does not establish missing Vulkan support. '
                                 'A working host Vulkan query does not verify 32-bit libraries or the Flatpak/Steam game runtime.'},
        unknowns=['GPU and active driver, selected Proton version, and match to the reported failing run are unknown.',
                  'No game was launched. Missing vulkaninfo is not proof of a broken GPU.']))


class BoundedLLM(LLM):
    def __init__(self, model):
        super().__init__(model=model)
        self.calls = 0
        self.response_metrics = []

    def chat(self, *args, **kwargs):
        self.calls += 1
        if self.calls > 8:
            raise LLMError('Evaluation stopped at its eight-model-call budget.')
        try:
            return super().chat(*args, **kwargs)
        finally:
            self.response_metrics.append(dict(self.last_response_metadata))


def evaluate(model_name: str) -> dict:
    cases = [
        ('vague_game', 'hey this games not working, fix it'),
        ('known_game', 'Steam/Proton game Example, AppID 480, closes immediately after Play. Inspect its installation and logs before suggesting changes.'),
        ('windows_share', 'Look up your bundled guidance about access denied on a Windows SMB share from Linux, then explain the checks. Do not inspect my computer.'),
    ]
    rows = []
    original_call = tools.call
    for name, idea in cases:
        db = Database(':memory:')
        llm = BoundedLLM(model_name)
        llm.ensure_model()
        if llm.model != model_name:
            raise ValueError('Requested model is unavailable; refusing a fallback.')
        task_id = db.add_task(idea)
        fixture_calls, blocked = [], []

        def guarded_call(tool_name, arguments, ctx):
            if tool_name not in INTERNAL_TOOLS and tool_name != 'steam_game_diagnostics':
                blocked.append({'tool': tool_name, 'args': arguments})
                raise ToolError('Evaluation blocked this computer tool. Explain remaining uncertainty; no action was executed.')
            return original_call(tool_name, arguments, ctx)

        def simulated_snapshot(app_id='', log_path=''):
            fixture_calls.append({'app_id': app_id, 'log_path': log_path})
            return fixture_diagnostic(app_id, log_path)

        started = time.monotonic()
        with patch.object(tools, 'call', guarded_call), \
                patch.object(tools.registry.REGISTRY['steam_game_diagnostics'], 'func', simulated_snapshot):
            agent = Agent(db, llm)
            first = agent.run(task_id)
            result = first
            if name == 'vague_game' and result.status is Status.AWAITING_INPUT:
                result = agent.resume_after_answer(task_id,
                    'Steam with Proton. The game is Example, AppID 480. It closes back to Play immediately, without an error dialog.')
            approvals = 0
            while result.status is Status.AWAITING_CONFIRMATION and approvals < 2:
                pending = db.pending_confirmation(task_id)
                step = next(s for s in db.get_steps(task_id) if s.id == pending.step_id)
                if step.tool != 'steam_game_diagnostics':
                    raise AssertionError('A non-fixture tool reached the approval driver.')
                approvals += 1
                result = agent.resume_after_confirm(task_id, True, pending.id)

        failures = []
        if name == 'vague_game' and first.status is not Status.AWAITING_INPUT:
            failures.append('Initial clarification did not enter awaiting-input.')
        if name != 'windows_share' and not any(c['app_id'] == '480' for c in fixture_calls):
            failures.append('Selected-game fixture diagnosis was not reached.')
        if name == 'windows_share' and not any(s.tool == 'linux_reference' and s.status == 'ok' for s in db.get_steps(task_id)):
            failures.append('Bundled references were not retrieved.')
        if blocked:
            failures.append('Additional computer tools were requested; proposals blocked and require manual review.')
        if result.status not in (Status.DONE, Status.AWAITING_INPUT):
            failures.append('Workflow did not return an answer or a question within the evaluation budget.')
        if any(m.get('done_reason') == 'length' or m.get('done') is False for m in llm.response_metrics):
            failures.append('A model generation was incomplete; protocol status does not establish a complete answer.')
        exported_task = db.get_task(task_id).to_dict()
        exported_task['messages'] = [{k: v for k, v in m.items() if k != 'thinking'}
                                     for m in exported_task['messages']]
        row = dict(case=name, first_status=first.status.value, final_status=result.status.value,
                   seconds=round(time.monotonic()-started, 2), model_calls=llm.calls,
                   response_metrics=llm.response_metrics,
                   fixture_approvals=approvals, fixture_calls=fixture_calls, blocked=blocked,
                   failures=failures, task=exported_task)
        rows.append(row)
        print(f"{name}: {row['first_status']} -> {row['final_status']}; {llm.calls} model calls; {failures}", flush=True)
    return dict(model=model_name, options=config.LLM_OPTIONS, think=config.THINK,
                system_prompt=system_prompt(), tool_schemas=tools.schemas(),
                scope='Real model and agent, synthetic diagnosis only; no live game was inspected, launched or fixed.',
                results=rows, passed=sum(not r['failures'] for r in rows), total=len(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=config.MODEL)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(f"{report['passed']}/{report['total']} workflow checks passed. Read the traces for response quality.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
