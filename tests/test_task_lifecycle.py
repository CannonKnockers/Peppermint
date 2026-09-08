import pytest
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database
from peppermint.common.models import Status
from peppermint.daemon.tools.registry import Context, ToolError, call
from tests.test_agent import FakeLLM, FakeMessage, FakeCall


def test_cancelled_queued_task_does_not_reach_model():
    db=Database(':memory:'); task=db.add_task('Hello'); model=FakeLLM([])
    db.set_status(task, Status.CANCELLED)
    assert Agent(db, model).run(task).status is Status.CANCELLED
    assert not model.calls


def test_cancel_during_generation_discards_late_tool_call():
    db=Database(':memory:'); task=db.add_task('Inspect memory')
    class Model(FakeLLM):
        def chat(self, *args):
            agent.cancel(task)
            return FakeMessage(tool_calls=[FakeCall('system_info', {'topic':'memory'})])
    agent=Agent(db, Model([]))
    assert agent.run(task).status is Status.CANCELLED
    assert not db.get_steps(task)
    assert db.get_task(task).status == 'cancelled'


def test_cancel_then_immediate_followup_cannot_revive_old_generation():
    db=Database(':memory:'); task=db.add_task('Initial')
    class Model(FakeLLM):
        def chat(self, *args):
            agent.cancel(task)
            agent.queue_follow_up(task, 'Next')
            return FakeMessage(content='Old answer')
    agent=Agent(db, Model([]))
    assert agent.run(task).status is Status.CANCELLED
    assert db.get_task(task).status == 'queued'
    assert not db.get_task(task).result
    agent.llm=FakeLLM([FakeMessage(content='New answer')])
    assert agent.run(task).text == 'New answer'


def test_cancel_invalidates_pending_approval(tmp_path):
    db=Database(':memory:'); task=db.add_task('Write')
    path=tmp_path/'file'
    agent=Agent(db, FakeLLM([FakeMessage(tool_calls=[FakeCall('run_shell', {'cmd':f'touch {path}'})])]))
    assert agent.run(task).status is Status.AWAITING_CONFIRMATION
    agent.cancel(task)
    assert agent.resume_after_confirm(task, True).status is Status.CANCELLED
    assert not path.exists()
    assert db.pending_confirmation(task) is None


def test_stale_answer_is_ignored():
    db=Database(':memory:'); task=db.add_task('Hi'); model=FakeLLM([FakeMessage(content='Hello')])
    agent=Agent(db,model); agent.run(task)
    before=db.get_messages(task)
    assert agent.resume_after_answer(task,'stale').status is Status.DONE
    assert db.get_messages(task) == before
    assert len(model.calls) == 1


def test_multi_call_reply_persists_only_the_one_proposed_action():
    db=Database(':memory:'); task=db.add_task('Inspect')
    model=FakeLLM([FakeMessage(tool_calls=[FakeCall('list_dir',{'path':'/tmp'}), FakeCall('system_info',{})])])
    assert Agent(db,model).run(task).status is Status.AWAITING_CONFIRMATION
    calls=[m for m in db.get_messages(task) if m.get('tool_calls')]
    assert len(calls[0]['tool_calls']) == 1
    assert len(db.get_steps(task)) == 1


def test_plan_done_requires_successful_same_task_evidence():
    db=Database(':memory:'); task=db.add_task('Plan'); other=db.add_task('Other')
    wrong=db.add_step(other,'read_file',{},'safe','ok','ok')
    bad=db.add_step(task,'run_shell',{},'risky','failed','error')
    for evidence in (wrong,bad,999):
        with pytest.raises(ToolError, match='successful'):
            call('set_plan',{'steps':[{'description':'Inspect','status':'done','evidence_step_id':evidence},
                                      {'description':'Verify','status':'pending'}]}, Context(task,db,require_approval=True))
    good=db.add_step(task,'performance_snapshot',{},'risky','{}','ok')
    result=call('set_plan',{'steps':[{'description':'Inspect','status':'done','evidence_step_id':good},
                                   {'description':'Verify','status':'pending'}]}, Context(task,db,require_approval=True))
    assert isinstance(result,str)
    assert db.get_task(task).plan[0]['status']=='done'


def test_unfinished_plan_does_not_get_completed_badge():
    db=Database(':memory:'); task=db.add_task('Plan')
    db.set_plan(task,[{'description':'Inspect','status':'pending'}, {'description':'Verify','status':'pending'}])
    agent=Agent(db,FakeLLM([FakeMessage(content='Everything is complete.')]))
    assert agent.run(task).status is Status.AWAITING_INPUT
    assert 'unfinished' in db.get_task(task).question


def test_performance_solution_finishes_with_measured_report_without_second_model_call(monkeypatch):
    import json
    from peppermint.daemon.tools import performance as p
    snapshot={'memory':{'total_bytes':16*p.GIB,'available_bytes':7*p.GIB},
              'gpu':{'devices':[{'name':'RTX 3060 Ti','total_mib':8192,'free_mib':900}]}}
    monkeypatch.setattr(p,'collect_snapshot',lambda *_: snapshot)
    db=Database(':memory:'); task=db.add_task('Show me solutions for two videos and 1-2 AI prompts. Include YouTube and Netflix.')
    model=FakeLLM([FakeMessage(tool_calls=[FakeCall('performance_snapshot',{})])])
    agent=Agent(db,model)
    assert agent.run(task).status is Status.AWAITING_CONFIRMATION
    result=agent.resume_after_confirm(task,True)
    assert result.status is Status.DONE
    assert len(model.calls)==1
    assert '7.00 GiB' in result.text and '900/8192 MiB' in result.text
    assert 'queue a second' in result.text and 'browser-hosted AI' in result.text
    assert 'How to verify:' in result.text and 'does not verify' in result.text
    assert 'changed no settings' in result.text
    assert db.get_task(task).messages[-1]['content']==result.text


def test_old_approval_id_does_not_approve_new_action():
    db=Database(':memory:'); task=db.add_task('Inspect two things')
    model=FakeLLM([
        FakeMessage(tool_calls=[FakeCall('system_info',{'topic':'memory'})]),
        FakeMessage(tool_calls=[FakeCall('system_info',{'topic':'os'})]),
    ])
    agent=Agent(db,model)
    agent.run(task)
    old_id=db.pending_confirmation(task).id
    agent.resume_after_confirm(task,True,old_id)
    current=db.pending_confirmation(task)
    assert current.id != old_id
    result=agent.resume_after_confirm(task,True,old_id)
    assert result.status is Status.AWAITING_CONFIRMATION
    assert db.pending_confirmation(task).id == current.id
    assert db.get_steps(task)[-1].status == 'pending'


def test_cancelled_status_cannot_be_overwritten_by_late_completion():
    db=Database(':memory:'); task=db.add_task('Hello')
    db.set_status(task,Status.CANCELLED)
    db.set_status(task,Status.DONE,result='Late response')
    assert db.get_task(task).status=='cancelled'
    assert not db.get_task(task).result
