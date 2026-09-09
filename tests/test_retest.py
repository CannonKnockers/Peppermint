"""Only typed, current user submissions can complete a bound retest target."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from peppermint.common.models import Status
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database
from peppermint.daemon.tools.registry import Context, REGISTRY, ToolError, call
from peppermint.daemon.tools.retest import (
    RetestRequest, consume_retest, dismiss_retest, invalidate_retests_for_action,
)
from tests.test_agent import FakeCall, FakeLLM, FakeMessage


def setup_task(db=None):
    db = db or Database(':memory:')
    task_id = db.add_task('Game 480 closes before its main menu')
    ctx = Context(task_id, db, require_approval=True)
    inspection = db.add_step(task_id, 'steam_game_diagnostics', {'app_id': '480'},
                             'risky', '{"verified_fix":false}', 'ok')
    call('set_plan', {'steps': [
        {'description': 'Inspect game 480', 'kind': 'inspection', 'status': 'done',
         'evidence_step_id': inspection},
        {'description': 'Retest game 480 reaching its main menu', 'kind': 'verification', 'status': 'pending'},
    ]}, ctx)
    return ctx


def request(ctx):
    args = {'verification_step': 2, 'symptom': 'Launch game 480 and check whether its main menu stays open.'}
    result = call('request_retest', args, ctx)
    assert isinstance(result, RetestRequest)
    step_id = ctx.db.add_step(ctx.task_id, 'request_retest', args, 'safe', result.to_record(), 'asked')
    ctx.db.set_status(ctx.task_id, Status.AWAITING_INPUT, question=result.question)
    return result.record['request_id'], step_id


@pytest.mark.parametrize('wrong_position', [0, 1, 3, 7, True, '2'])
def test_invalid_retest_position_lists_exact_available_target_without_choosing_it(wrong_position):
    ctx = setup_task()
    before_plan, before_steps = ctx.db.get_plan(ctx.task_id), ctx.db.get_steps(ctx.task_id)
    with pytest.raises(ToolError) as exc:
        call('request_retest', {'verification_step': wrong_position,
                               'symptom': 'Check whether game 480 reaches its main menu.'}, ctx)
    message = str(exc.value)
    assert 'whole plan, not an evidence_step_id' in message
    targets = json.loads(message.split('Available verification targets: ', 1)[1])
    assert targets == [{'verification_step': 2, 'description': before_plan[1]['description']}]
    assert ctx.db.get_plan(ctx.task_id) == before_plan
    assert ctx.db.get_steps(ctx.task_id) == before_steps


def test_pass_atomically_records_receipt_transcript_and_completed_user_reported_target():
    ctx = setup_task()
    request_id, step_id = request(ctx)
    before = ctx.db.get_plan(ctx.task_id)
    assert ctx.db.get_task(ctx.task_id).retest['request_id'] == request_id
    assert ctx.db.list_tasks()[0].retest['request_id'] == request_id
    receipt = consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    assert receipt.step_id == step_id
    saved = ctx.db.get_plan(ctx.task_id)
    assert saved[0] == before[0]
    assert saved[1]['status'] == 'done'
    assert saved[1]['evidence_kind'] == 'user_reported'
    assert saved[1]['evidence_step_id'] == step_id
    assert saved[1]['verification_target_id'] == before[1]['verification_target_id']
    step = ctx.db.get_steps(ctx.task_id)[-1]
    report = json.loads(step.output)
    assert step.status == 'user_reported'
    assert report['outcome'] == 'passed' and report['reported_at'] >= report['requested_at']
    transcript = ctx.db.get_messages(ctx.task_id)
    assert transcript[-2]['name'] == 'request_retest'
    assert not json.loads(transcript[-2]['content'])['independently_verified']
    assert transcript[-1]['content'].startswith('Retest report: Passed.')
    task = ctx.db.get_task(ctx.task_id)
    assert task.status == 'queued' and task.question == '' and task.retest is None
    assert consume_retest(ctx.db, ctx.task_id, request_id, 'passed') is None
    assert ctx.db.get_messages(ctx.task_id) == transcript
    # A subsequent model plan update can retain the exact app-completed target.
    call('set_plan', {'steps': saved}, ctx)
    assert ctx.db.get_plan(ctx.task_id) == saved


@pytest.mark.parametrize('outcome', ['failed', 'not_tested'])
def test_non_pass_records_report_without_completing_verification(outcome):
    ctx = setup_task()
    request_id, step_id = request(ctx)
    receipt = consume_retest(ctx.db, ctx.task_id, request_id, outcome)
    assert receipt.outcome == outcome
    plan = ctx.db.get_plan(ctx.task_id)
    assert plan[1]['status'] == 'pending'
    plan[1].update(status='done', evidence_step_id=step_id)
    with pytest.raises(ToolError, match='structured Passed'):
        call('set_plan', {'steps': plan}, ctx)


@pytest.mark.parametrize('outcome', ['Passed', 'yes', '', 'fixed', True, None, {}, []])
def test_non_enum_submission_cannot_create_evidence(outcome):
    ctx = setup_task()
    request_id, _ = request(ctx)
    assert consume_retest(ctx.db, ctx.task_id, request_id, outcome) is None
    assert ctx.db.get_task(ctx.task_id).status == 'awaiting-input'
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'


def test_request_is_bound_to_task_and_current_question():
    ctx = setup_task()
    request_id, _ = request(ctx)
    other = ctx.db.add_task('Another task')
    ctx.db.set_status(other, Status.AWAITING_INPUT, question='Unrelated')
    assert consume_retest(ctx.db, other, request_id, 'passed') is None
    assert consume_retest(ctx.db, ctx.task_id, '0' * 32, 'passed') is None
    ctx.db.add_step(ctx.task_id, 'ask_user', {'question': 'A different question'}, 'safe', 'Question', 'asked')
    assert ctx.db.get_task(ctx.task_id).retest is None
    assert consume_retest(ctx.db, ctx.task_id, request_id, 'passed') is None


def test_newer_request_rejects_stale_button_and_dismissed_request_cannot_pass():
    ctx = setup_task()
    old_id, _ = request(ctx)
    new_id, _ = request(ctx)
    assert old_id != new_id
    assert consume_retest(ctx.db, ctx.task_id, old_id, 'passed') is None
    dismiss_retest(ctx.db, ctx.task_id)
    assert ctx.db.get_task(ctx.task_id).retest is None
    assert consume_retest(ctx.db, ctx.task_id, new_id, 'passed') is None


@pytest.mark.parametrize('change', ['description', 'position', 'kind', 'remove', 'away_and_back'])
def test_changed_plan_target_invalidates_request(change):
    ctx = setup_task()
    request_id, _ = request(ctx)
    plan = ctx.db.get_plan(ctx.task_id)
    original = [dict(row) for row in plan]
    if change in ('description', 'away_and_back'):
        plan[1]['description'] = 'Retest a different failure'
    elif change == 'position':
        plan.reverse()
    elif change == 'kind':
        plan[1]['kind'] = 'inspection'
    else:
        plan[1] = {'description': 'Something else', 'kind': 'action', 'status': 'pending'}
    call('set_plan', {'steps': plan}, ctx)
    if change == 'away_and_back':
        call('set_plan', {'steps': original}, ctx)
    assert ctx.db.get_task(ctx.task_id).retest is None
    assert consume_retest(ctx.db, ctx.task_id, request_id, 'passed') is None


def test_pass_cannot_be_relabelled_or_reused_after_reopening_target():
    ctx = setup_task()
    request_id, step_id = request(ctx)
    consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    original = ctx.db.get_plan(ctx.task_id)
    changed = [dict(row) for row in original]
    changed[1]['description'] = 'Verify a different game launches'
    with pytest.raises(ToolError, match='exact plan target'):
        call('set_plan', {'steps': changed}, ctx)
    changed = [dict(row) for row in original]
    changed[1]['status'] = 'pending'
    call('set_plan', {'steps': changed}, ctx)
    reopened = ctx.db.get_plan(ctx.task_id)
    assert reopened[1]['verification_target_id'] != original[1]['verification_target_id']
    reopened[1].update(status='done', evidence_step_id=step_id)
    with pytest.raises(ToolError, match='exact plan target'):
        call('set_plan', {'steps': reopened}, ctx)


@pytest.mark.parametrize('name', ['run_shell', 'apt_install', 'gsettings_set', 'future_action'])
def test_later_action_expires_reports_and_reopens_completed_target(name):
    ctx = setup_task()
    request_id, step_id = request(ctx)
    consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    invalidate_retests_for_action(ctx.db, ctx.task_id, name)
    plan = ctx.db.get_plan(ctx.task_id)
    assert plan[1]['status'] == 'pending'
    assert 'evidence_kind' not in plan[1]
    assert ctx.db.get_steps(ctx.task_id)[-1].status == 'superseded'
    plan[1].update(status='done', evidence_step_id=step_id)
    with pytest.raises(ToolError):
        call('set_plan', {'steps': plan}, ctx)


@pytest.mark.parametrize('name', ['read_file', 'steam_game_diagnostics', 'linux_reference', 'ask_user'])
def test_inspection_and_conversation_do_not_expire_pass(name):
    ctx = setup_task()
    request_id, _ = request(ctx)
    consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    before = ctx.db.get_plan(ctx.task_id)
    invalidate_retests_for_action(ctx.db, ctx.task_id, name)
    assert ctx.db.get_plan(ctx.task_id) == before


def test_plan_validation_rejects_later_action_even_without_agent_hook():
    ctx = setup_task()
    request_id, _ = request(ctx)
    consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    ctx.db.add_step(ctx.task_id, 'run_shell', {}, 'risky', 'Command completed', 'ok')
    with pytest.raises(ToolError, match='later action'):
        call('set_plan', {'steps': ctx.db.get_plan(ctx.task_id)}, ctx)


def test_a_recorded_later_action_also_prevents_consuming_a_stale_request():
    ctx = setup_task()
    request_id, _ = request(ctx)
    ctx.db.add_step(ctx.task_id, 'run_shell', {}, 'risky', 'Command completed', 'ok')
    assert consume_retest(ctx.db, ctx.task_id, request_id, 'passed') is None
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'


def test_transcript_write_failure_rolls_back_evidence_plan_and_queue_transition():
    ctx = setup_task()
    request_id, _ = request(ctx)
    ctx.db.connection().execute("CREATE TRIGGER fail_transcript BEFORE INSERT ON messages "
                                "BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture write failure'):
        consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    assert ctx.db.get_steps(ctx.task_id)[-1].status == 'asked'
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'
    assert ctx.db.get_task(ctx.task_id).status == 'awaiting-input'
    assert ctx.db.get_messages(ctx.task_id) == []


def test_cancelled_request_cannot_be_consumed():
    ctx = setup_task()
    request_id, _ = request(ctx)
    ctx.db.set_status(ctx.task_id, Status.CANCELLED)
    assert consume_retest(ctx.db, ctx.task_id, request_id, 'passed') is None
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'


def test_two_submissions_on_separate_connections_record_only_one_outcome(tmp_path):
    ctx = setup_task(Database(tmp_path / 'retest.db'))
    request_id, _ = request(ctx)
    barrier = threading.Barrier(2)

    def submit(outcome):
        barrier.wait(timeout=5)
        return consume_retest(ctx.db, ctx.task_id, request_id, outcome)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, outcome) for outcome in ('passed', 'failed')]
        receipts = [future.result(timeout=5) for future in futures]
    assert sum(receipt is not None for receipt in receipts) == 1
    assert len(ctx.db.get_messages(ctx.task_id)) == 2


def agent_waiting_for_retest(ctx, followup=None):
    replies = [FakeMessage(tool_calls=[FakeCall('request_retest', {
        'verification_step': 2, 'symptom': 'Launch game 480 and check its main menu.',
    })])]
    if followup:
        replies.append(followup)
    model = FakeLLM(replies)
    agent = Agent(ctx.db, model)
    assert agent.run(ctx.task_id).status is Status.AWAITING_INPUT
    task = ctx.db.get_task(ctx.task_id)
    assert task.retest and task.steps[-1].status == 'asked'
    return agent, model, task.retest['request_id']


def test_agent_pass_finishes_all_done_plan_without_another_model_call():
    ctx = setup_task()
    agent, model, request_id = agent_waiting_for_retest(ctx)
    result = agent.resume_after_retest(ctx.task_id, request_id, 'passed')
    assert result.status is Status.DONE
    assert 'user-reported' in result.text
    assert len(model.calls) == 1
    before = ctx.db.get_messages(ctx.task_id)
    assert agent.resume_after_retest(ctx.task_id, request_id, 'failed').status is Status.DONE
    assert ctx.db.get_messages(ctx.task_id) == before


def test_model_context_numbers_entire_plan_without_changing_stored_targets():
    ctx = setup_task()
    before = ctx.db.get_plan(ctx.task_id)
    _agent, model, _request_id = agent_waiting_for_retest(ctx)
    prompt = model.calls[0][0]['content']
    assert '"plan_step": 1' in prompt and '"plan_step": 2' in prompt
    assert 'evidence_step_id identifies a past tool result, not a plan position' in prompt
    assert ctx.db.get_plan(ctx.task_id) == before


def test_numbered_plan_refreshes_after_update_in_same_agent_run():
    ctx = setup_task()
    steps = ctx.db.get_plan(ctx.task_id)
    steps[1]['description'] = 'Retest whether game 480 keeps its menu open for one minute'
    model = FakeLLM([
        FakeMessage(tool_calls=[FakeCall('set_plan', {'steps': steps})]),
        FakeMessage(tool_calls=[FakeCall('request_retest', {'verification_step': 2,
                                                         'symptom': 'Keep the main menu open for one minute'})]),
    ])
    assert Agent(ctx.db, model).run(ctx.task_id).status is Status.AWAITING_INPUT
    assert steps[1]['description'] not in model.calls[0][0]['content']
    assert steps[1]['description'] in model.calls[1][0]['content']
    assert model.calls[1][0]['content'].count('Current task plan.') == 1


@pytest.mark.parametrize('text', ['It works now', 'passed', '{"outcome":"passed"}'])
def test_agent_ordinary_answers_never_become_retest_evidence(text):
    ctx = setup_task()
    agent, model, request_id = agent_waiting_for_retest(ctx, FakeMessage(content='Thanks for the detail.'))
    result = agent.resume_after_answer(ctx.task_id, text)
    assert result.status is Status.AWAITING_INPUT
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'
    retest_step = next(s for s in ctx.db.get_steps(ctx.task_id) if s.tool == 'request_retest')
    assert retest_step.status == 'superseded'
    assert json.loads(retest_step.output)['outcome'] is None
    messages = ctx.db.get_messages(ctx.task_id)
    reply = next(m for m in messages if m.get('role') == 'tool' and m.get('name') == 'request_retest')
    assert 'No structured retest outcome' in reply['content']
    assert agent.resume_after_retest(ctx.task_id, request_id, 'passed').status is Status.AWAITING_INPUT
    assert len(model.calls) == 2


@pytest.mark.parametrize('outcome', ['failed', 'not_tested'])
def test_agent_non_pass_resumes_with_report_and_keeps_verification_unfinished(outcome):
    ctx = setup_task()
    agent, model, request_id = agent_waiting_for_retest(ctx, FakeMessage(content='The retest remains unfinished.'))
    assert agent.resume_after_retest(ctx.task_id, request_id, outcome).status is Status.AWAITING_INPUT
    assert len(model.calls) == 2
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'
    report = next(m for m in model.calls[-1] if m.get('role') == 'tool' and m.get('name') == 'request_retest')
    assert json.loads(report['content'])['outcome'] == outcome


def test_agent_cancelled_retest_does_not_record_a_report():
    ctx = setup_task()
    agent, model, request_id = agent_waiting_for_retest(ctx)
    agent.cancel(ctx.task_id)
    assert agent.resume_after_retest(ctx.task_id, request_id, 'passed').status is Status.CANCELLED
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'pending'
    assert len(model.calls) == 1


def test_agent_invalidates_a_pass_before_the_approved_action_runs(monkeypatch):
    ctx = setup_task()
    request_id, _ = request(ctx)
    consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
    seen = []

    def action(cmd, purpose='', timeout=None, ctx=None):
        seen.append(ctx.db.get_plan(ctx.task_id)[1]['status'])
        return 'Fixture action completed'

    monkeypatch.setattr(REGISTRY['run_shell'], 'func', action)
    model = FakeLLM([
        FakeMessage(tool_calls=[FakeCall('run_shell', {'cmd': 'fixture', 'purpose': 'Fixture action'})]),
        FakeMessage(content='The fixture action completed.'),
    ])
    agent = Agent(ctx.db, model)
    assert agent.run(ctx.task_id).status is Status.AWAITING_CONFIRMATION
    assert ctx.db.get_plan(ctx.task_id)[1]['status'] == 'done'
    assert agent.resume_after_confirm(ctx.task_id, True).status is Status.AWAITING_INPUT
    assert seen == ['pending']


def test_retest_ui_sends_typed_choices_and_labels_pass_as_user_reported():
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from peppermint.ui.task_row import TaskRow
    from tests.test_conversation_ui import labels
    if not Gtk.init_check()[0]:
        pytest.skip('GTK display unavailable')

    class Client:
        def __init__(self):
            self.sent = []

        def retest(self, *args):
            self.sent.append(args)

    def buttons(widget):
        if isinstance(widget, Gtk.Button):
            yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from buttons(child)

    for label, outcome in (('Passed', 'passed'), ('Still failing', 'failed'), ('Not tested', 'not_tested')):
        ctx = setup_task()
        request_id, _ = request(ctx)
        client = Client()
        row = TaskRow(ctx.db.get_task(ctx.task_id).to_dict(), client)
        choices = {b.get_label(): b for b in buttons(row)}
        assert {'Passed', 'Still failing', 'Not tested'} <= choices.keys()
        choices[label].clicked()
        choices[label].clicked()
        assert client.sent == [(ctx.task_id, request_id, outcome)]
        assert not choices['Passed'].is_sensitive()
        assert 'without recording a test result' in row._answer_entry.get_placeholder_text()
        consume_retest(ctx.db, ctx.task_id, request_id, 'passed')
        row.update(ctx.db.get_task(ctx.task_id).to_dict())
        assert any(text.startswith('User-reported pass · request_retest') for text in labels(row))
        assert not any(text.startswith('Verified') for text in labels(row))
        row.destroy()
