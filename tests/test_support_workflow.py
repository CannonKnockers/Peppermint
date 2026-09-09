"""Integration boundaries for sourced guidance and approved game diagnostics."""

import json

import pytest

from peppermint.common.models import Status
from peppermint.daemon import tools
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database
from peppermint.daemon.tools.registry import Context, INTERNAL_TOOLS, ToolError
from tests.test_agent import FakeCall, FakeLLM, FakeMessage


def test_reference_lookup_does_not_approve_following_computer_action(monkeypatch):
    probe_calls = []
    monkeypatch.setattr(tools.registry.REGISTRY['steam_game_diagnostics'], 'func',
                        lambda app_id='', log_path='': probe_calls.append(app_id) or '{}')
    db = Database(':memory:')
    task = db.add_task('Investigate Steam game 480.')
    model = FakeLLM([
        FakeMessage(tool_calls=[FakeCall('linux_reference', {'query': 'Steam Proton game launch'})]),
        FakeMessage(tool_calls=[FakeCall('steam_game_diagnostics', {'app_id': '480'})]),
        FakeMessage(content='The check was denied; no inspection or fix was made.'),
    ])
    agent = Agent(db, model)
    assert agent.run(task).status is Status.AWAITING_CONFIRMATION
    assert not probe_calls
    reference, diagnostic = db.get_steps(task)
    assert reference.status == 'ok'
    assert json.loads(reference.output)['results']
    assert diagnostic.status == 'pending'
    assert agent.resume_after_confirm(task, False).status is Status.DONE
    assert not probe_calls
    assert db.get_steps(task)[-1].status == 'denied'


def test_approved_diagnostics_and_reference_keep_complete_evidence(monkeypatch):
    report = json.dumps({'app_id': '480', 'log': 'x' * 5200, 'no_verified_fix': True})
    calls = []
    monkeypatch.setattr(tools.registry.REGISTRY['steam_game_diagnostics'], 'func',
                        lambda app_id='', log_path='': calls.append({'app_id': app_id}) or report)
    db = Database(':memory:')
    task = db.add_task('Inspect Steam game 480, then explain what to check.')
    model = FakeLLM([
        FakeMessage(tool_calls=[FakeCall('steam_game_diagnostics', {'app_id': '480'})]),
        FakeMessage(tool_calls=[FakeCall('linux_reference', {'query': 'Steam Proton Vulkan'})]),
        FakeMessage(content='The checks completed. The game has not been retested or fixed.'),
    ])
    agent = Agent(db, model)
    assert agent.run(task).status is Status.AWAITING_CONFIRMATION
    assert not calls
    assert agent.resume_after_confirm(task, True).status is Status.DONE
    assert calls == [{'app_id': '480'}]
    assert db.get_steps(task)[0].output == report
    evidence = [m for m in model.calls[-1] if m['role'] == 'tool']
    for message in evidence:
        payload = json.loads(message['content'].split('\n', 1)[1])
        assert payload
    assert json.loads(evidence[0]['content'].split('\n', 1)[1])['no_verified_fix']
    assert json.loads(evidence[-1]['content'].split('\n', 1)[1])['results'][0]['sources']


def test_reference_cannot_prove_a_plan_step_completed():
    db = Database(':memory:')
    task = db.add_task('Repair an application')
    reference = db.add_step(task, 'linux_reference', {'query': 'Wine'}, 'safe', '{}', 'ok')
    with pytest.raises(ToolError, match='successful computer-tool'):
        tools.call('set_plan', {'steps': [
            {'description': 'Repair application', 'status': 'done', 'evidence_step_id': reference},
            {'description': 'Verify launch', 'status': 'pending'},
        ]}, Context(task, db, require_approval=True))
    assert not db.get_plan(task)


def test_selected_log_changed_since_approval_is_not_read(tmp_path, monkeypatch):
    path = tmp_path / 'steam-480.log'
    path.write_text('Original launch output')
    reads = []
    monkeypatch.setattr(tools.registry.REGISTRY['steam_game_diagnostics'], 'func',
                        lambda app_id='', log_path='': reads.append(log_path) or '{}')
    db = Database(':memory:')
    task = db.add_task('Inspect this selected game log')
    agent = Agent(db, FakeLLM([
        FakeMessage(tool_calls=[FakeCall('steam_game_diagnostics', {'app_id': '480', 'log_path': str(path)})]),
        FakeMessage(content='The log changed; inspection was refused.'),
    ]))
    assert agent.run(task).status is Status.AWAITING_CONFIRMATION
    path.write_text('Different launch output with a different size')
    assert agent.resume_after_confirm(task, True).status is Status.DONE
    assert not reads
    assert db.get_steps(task)[0].status == 'refused'


def test_every_computer_tool_still_pauses_before_its_function(monkeypatch):
    # Populate required parameters, then replace functions with a side-effect
    # sentinel. The central gate must run before any computer-tool code.
    for name, entry in tools.registry.REGISTRY.items():
        if name in INTERNAL_TOOLS:
            continue
        touched = []
        original = entry.func

        def sentinel(**kwargs):
            touched.append(kwargs)

        import inspect
        sentinel.__signature__ = inspect.signature(original)
        monkeypatch.setattr(entry, 'func', sentinel)
        arguments = {key: 'fixture' for key in entry.parameters.get('required', [])}
        assert isinstance(tools.call(name, arguments, Context(1, require_approval=True)), tools.Confirm), name
        assert not touched, name
