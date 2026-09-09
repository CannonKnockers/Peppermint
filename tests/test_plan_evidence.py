"""Plan completion records bounded evidence scope, not model claims of success."""

import json

import pytest

from peppermint.daemon.db import Database
from peppermint.daemon.tools.planning import ACTION_TOOLS, INSPECTION_TOOLS
from peppermint.daemon.tools.registry import (
    Context, INTERNAL_TOOLS, REGISTRY, ToolError, call,
)


@pytest.fixture
def task_context():
    db = Database(':memory:')
    return Context(db.add_task('Investigate a Steam game'), db, require_approval=True)


def save_step(ctx, step):
    return call('set_plan', {'steps': [
        step,
        {'description': 'Retest the original symptom', 'kind': 'verification', 'status': 'pending'},
    ]}, ctx)


def completed(step_id, kind='inspection', description='Inspect the installation'):
    return {'description': description, 'kind': kind, 'status': 'done',
            'evidence_step_id': step_id}


@pytest.mark.parametrize('kind, description', [
    ('action', 'Repair game'),
    ('verification', 'Verify game launches'),
])
@pytest.mark.parametrize('output', [
    {'verified_fix': False, 'changed_settings': False},
    {'verified_fix': True, 'changed_settings': True},
])
def test_game_diagnostics_never_complete_action_or_verification(task_context, kind, description, output):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'steam_game_diagnostics', {'app_id': '480'},
                           'risky', json.dumps(output), 'ok')
    with pytest.raises(ToolError, match='cannot complete|structured Passed'):
        save_step(ctx, completed(step, kind, description))
    assert ctx.db.get_plan(ctx.task_id) == []


def test_game_inspection_can_finish_with_explicit_scope(task_context):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'steam_game_diagnostics', {'app_id': '480'},
                           'risky', '{"verified_fix":false}', 'ok')
    save_step(ctx, completed(step))
    saved, retest = ctx.db.get_plan(ctx.task_id)
    assert saved == {
        'description': 'Inspect the installation', 'kind': 'inspection', 'status': 'done',
        'evidence_step_id': step, 'evidence_kind': 'inspection',
        'evidence_tool': 'steam_game_diagnostics',
    }
    assert retest['status'] == 'pending'
    assert 'evidence_kind' not in retest


def test_app_derives_evidence_scope_and_tool_instead_of_trusting_model_metadata(task_context):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'performance_snapshot', {}, 'risky', '{}', 'ok')
    item = completed(step, description='A model-written description is not an attestation')
    item.update(evidence_kind='verification', evidence_tool='verified_game_launch')
    save_step(ctx, item)
    saved = ctx.db.get_plan(ctx.task_id)[0]
    assert saved['evidence_kind'] == 'inspection'
    assert saved['evidence_tool'] == 'performance_snapshot'


@pytest.mark.parametrize('tool', sorted(ACTION_TOOLS))
def test_action_completion_has_limited_scope_and_cannot_prove_verification(task_context, tool):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, tool, {}, 'risky', 'Operation completed', 'ok')
    save_step(ctx, completed(step, 'action', 'Perform the requested operation'))
    saved = ctx.db.get_plan(ctx.task_id)[0]
    assert saved['evidence_kind'] == ('command' if tool == 'run_shell' else 'action')
    assert saved['evidence_tool'] == tool
    with pytest.raises(ToolError, match='cannot complete an inspection'):
        save_step(ctx, completed(step, 'inspection'))
    with pytest.raises(ToolError, match='structured Passed'):
        save_step(ctx, completed(step, 'verification', 'Verify the original symptom'))


@pytest.mark.parametrize('tool', sorted(INSPECTION_TOOLS))
def test_read_only_tool_completes_only_inspection(task_context, tool):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, tool, {}, 'safe', 'Observed data', 'ok')
    save_step(ctx, completed(step))
    with pytest.raises(ToolError, match='cannot complete an action'):
        save_step(ctx, completed(step, 'action', 'Apply the repair'))


@pytest.mark.parametrize('status', ['pending', 'denied', 'refused', 'error'])
def test_non_success_is_not_evidence_of_any_completed_scope(task_context, status):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'steam_game_diagnostics', {}, 'risky', '{}', status)
    with pytest.raises(ToolError, match='successful computer-tool'):
        save_step(ctx, completed(step))


def test_unknown_successful_tool_fails_closed(task_context):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'future_game_repair', {}, 'risky', 'verified', 'ok')
    for kind in ('inspection', 'action', 'verification'):
        with pytest.raises(ToolError, match='cannot complete|structured Passed'):
            save_step(ctx, completed(step, kind))


def test_completion_requires_kind_even_for_legacy_steps(task_context):
    ctx = task_context
    step = ctx.db.add_step(ctx.task_id, 'steam_game_diagnostics', {}, 'risky', '{}', 'ok')
    item = completed(step)
    del item['kind']
    with pytest.raises(ToolError, match='must declare kind'):
        save_step(ctx, item)


def test_old_unfinished_plans_remain_editable_without_invented_scope(task_context):
    ctx = task_context
    save_step(ctx, {'description': 'Inspect', 'status': 'in_progress'})
    saved = ctx.db.get_plan(ctx.task_id)[0]
    assert saved == {'description': 'Inspect', 'status': 'in_progress', 'evidence_step_id': 0}


def test_unfinished_steps_do_not_retain_forged_completion_metadata(task_context):
    ctx = task_context
    save_step(ctx, {'description': 'Retest', 'status': 'pending', 'kind': 'verification',
                    'evidence_kind': 'verification', 'evidence_tool': 'run_shell'})
    saved = ctx.db.get_plan(ctx.task_id)[0]
    assert 'evidence_kind' not in saved
    assert 'evidence_tool' not in saved


@pytest.mark.parametrize('kind', ['', 'repair', 'verified', 4, [], {}])
def test_invalid_scope_is_rejected_even_for_pending_steps(task_context, kind):
    with pytest.raises(ToolError, match='Step kind must'):
        save_step(task_context, {'description': 'Inspect', 'status': 'pending', 'kind': kind})


def test_failed_completion_update_preserves_existing_plan(task_context):
    ctx = task_context
    save_step(ctx, {'description': 'Inspect', 'status': 'pending', 'kind': 'inspection'})
    before = ctx.db.get_plan(ctx.task_id)
    step = ctx.db.add_step(ctx.task_id, 'steam_game_diagnostics', {}, 'risky', '{}', 'ok')
    with pytest.raises(ToolError):
        save_step(ctx, completed(step, 'action', 'Repair game'))
    assert ctx.db.get_plan(ctx.task_id) == before


def test_every_current_tool_has_an_explicit_evidence_scope_or_is_internal():
    assert INSPECTION_TOOLS.isdisjoint(ACTION_TOOLS)
    assert (INSPECTION_TOOLS | ACTION_TOOLS).isdisjoint(INTERNAL_TOOLS)
    assert set(REGISTRY) == INSPECTION_TOOLS | ACTION_TOOLS | INTERNAL_TOOLS
